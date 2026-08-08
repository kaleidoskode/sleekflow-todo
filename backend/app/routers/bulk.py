"""Batch status changes and deletes.

Registered **before** the todo routes in `create_app`, and that ordering is
load-bearing: `/api/todos/bulk/status` also matches `/api/todos/{todo_id}/status`,
and FastAPI takes the first route that matches. Registered the other way round,
`bulk` is parsed as a `todo_id`, fails UUID validation, and every request here
returns 422. `tests/integration/test_bulk.py` pins the ordering.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_session_factory
from app.core.deps import current_user
from app.core.events import broker
from app.models.user import User
from app.schemas.bulk import BulkDelete, BulkResult, BulkStatusChange
from app.services.bulk_service import BulkService

router = APIRouter(
    prefix="/api/todos/bulk",
    tags=["bulk"],
    dependencies=[Depends(current_user)],
)

_PARTIAL_DOC = (
    "Always ``200``: the batch itself succeeded, and the per-item outcomes are "
    "the payload. Check ``failed`` and the ``ok`` flag on each result — a "
    "blocked or stale item fails alone and the rest still apply."
)


def _announce(action: str, result: BulkResult, actor: str, **extra: object) -> None:
    """One event for the whole batch, not one per item.

    Every event costs each watching tab a refetch, so announcing 200 individual
    changes would turn one click into 200 rounds of invalidation per client.
    The batch is a single thing that happened, so it is a single event.
    """
    if result.succeeded == 0:
        return  # nothing committed, so nothing to tell anyone about
    broker.publish(
        {"action": action, "count": result.succeeded, "actor": actor, **extra}
    )


@router.post(
    "/status",
    response_model=BulkResult,
    summary="Change the status of many todos",
    description=(
        "Applies one status to every listed todo. Each entry carries its own "
        "``version`` — a batch cannot use ``If-Match``, which is a single "
        "header, so the precondition moves into the body and stays per row.\n\n"
        + _PARTIAL_DOC
    ),
    response_description="Per-item outcomes, in the order they were sent.",
    responses={422: {"description": "Empty list, duplicate ids, or over the item limit."}},
)
async def bulk_change_status(
    payload: BulkStatusChange,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    user: User = Depends(current_user),
) -> BulkResult:
    result = await BulkService(session_factory, user.id).change_status(
        payload.items, payload.status
    )
    _announce("bulk_status_changed", result, user.username, status=payload.status.value)
    return result


@router.post(
    "/delete",
    response_model=BulkResult,
    summary="Soft-delete many todos",
    description=(
        "Soft-deletes every listed todo. Nothing is destroyed — each row keeps "
        "its ``deleted_at`` and can be restored individually.\n\n" + _PARTIAL_DOC
    ),
    response_description="Per-item outcomes, in the order they were sent.",
    responses={422: {"description": "Empty list, duplicate ids, or over the item limit."}},
)
async def bulk_delete(
    payload: BulkDelete,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    user: User = Depends(current_user),
) -> BulkResult:
    result = await BulkService(session_factory, user.id).delete(payload.items)
    _announce("bulk_deleted", result, user.username)
    return result
