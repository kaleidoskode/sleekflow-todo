"""TODO CRUD, listing, status transitions, and restore.

Every mutating endpoint requires an ``If-Match`` header carrying the row
``version`` (absent → 428, stale → 409 with the current server state).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import current_user
from app.core.errors import MalformedPrecondition, PreconditionRequired
from app.core.events import publish_todo_change
from app.core.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SortSpec
from app.domain.enums import Status
from app.models.todo import Todo
from app.models.user import User
from app.repositories.dependency_repo import DependencyRepository
from app.repositories.todo_repo import TodoFilter
from app.repositories.user_repo import UserRepository
from app.schemas.todo import (
    NAME_TO_PRIORITY,
    DependencyRead,
    StatusChange,
    StatusChangeResult,
    TodoCreate,
    TodoPage,
    TodoRead,
    TodoUpdate,
)
from app.services.status_service import StatusService
from app.services.todo_service import TodoService

router = APIRouter(
    prefix="/api/todos",
    tags=["todos"],
    # Applied at the router so no endpoint can be added unprotected by
    # accident. The list is shared: this gates access, it does not scope data.
    dependencies=[Depends(current_user)],
)

_BLOCKED_DOC = "Show only blocked (true) or unblocked (false) todos. Omit to see both."
_DELETED_DOC = "Include soft-deleted todos in the response."
_SORT_DOC = (
    "Sort field, optionally prefixed with ``-`` for descending. "
    "Allowed: due_date, priority, status, name."
)
_CURSOR_DOC = "Opaque cursor from a previous ``next_cursor`` to get the next page."
_LIMIT_DOC = f"Items per page (1–{MAX_PAGE_SIZE}, default {DEFAULT_PAGE_SIZE})."


def require_if_match(request: Request) -> int:
    raw = request.headers.get("if-match")
    if raw is None:
        raise PreconditionRequired("This request requires an If-Match header carrying the version.")
    candidate = raw.strip().removeprefix("W/").strip('"')
    try:
        return int(candidate)
    except ValueError as exc:
        raise MalformedPrecondition(
            f"If-Match must carry a numeric version, got {raw!r}."
        ) from exc


def _set_etag(response: Response, todo: Todo) -> None:
    """The version, as an ETag. Callers send it back as `If-Match`."""
    response.headers["ETag"] = f'"{todo.version}"'


def _with_etag(
    response: Response,
    todo: Todo,
    updated_by: str | None = None,
    depends_on: list[DependencyRead] | None = None,
) -> TodoRead:
    _set_etag(response, todo)
    return TodoRead.from_todo(todo, depends_on=depends_on, updated_by=updated_by)


@router.post(
    "",
    response_model=TodoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a todo",
    response_description="The created todo. Its ``version`` starts at 1.",
)
async def create_todo(
    payload: TodoCreate,
    response: Response,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> TodoRead:
    todo = await TodoService(session, user.id).create(payload)
    # Published here rather than in the service because the router already
    # holds the User: the service has only an actor_id, so announcing from
    # there would cost a query per write purely to resolve a username. The
    # service commits before returning, so this is still strictly post-commit.
    publish_todo_change("created", todo, user.username)
    return _with_etag(response, todo, user.username)


@router.get(
    "",
    response_model=TodoPage,
    summary="List todos",
    description=(
        "Keyset-paginated, server-filtered listing. "
        "The ``next_cursor`` is an opaque token — pass it back as ``cursor`` "
        "to walk forward.  No offset: page 100 costs the same as page 1."
    ),
)
async def list_todos(
    status_filter: list[Status] = Query(
        default=[],
        alias="status",
        description="Filter by status. Repeat for multiple (e.g. ``?status=in_progress&status=completed``).",
    ),
    priority: list[str] = Query(
        default=[],
        description="Filter by priority (low, medium, high). Repeat for multiple.",
    ),
    due_before: datetime | None = Query(
        default=None, description="Only todos due before this timestamp."
    ),
    due_after: datetime | None = Query(
        default=None, description="Only todos due after this timestamp."
    ),
    blocked: bool | None = Query(default=None, description=_BLOCKED_DOC),
    include_deleted: bool = Query(default=False, description=_DELETED_DOC),
    sort: str = Query(default="due_date", description=_SORT_DOC),
    cursor: str | None = Query(default=None, description=_CURSOR_DOC),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description=_LIMIT_DOC),
    session: AsyncSession = Depends(get_session),
) -> TodoPage:
    try:
        sort_spec = SortSpec.parse(sort)
        priorities = [int(NAME_TO_PRIORITY[p]) for p in priority]
    except (ValueError, KeyError) as exc:
        raise RequestValidationError([{"loc": ("query", "sort"), "msg": str(exc)}]) from exc

    filters = TodoFilter(
        statuses=status_filter,
        priorities=priorities,
        due_before=due_before,
        due_after=due_after,
        blocked=blocked,
        include_deleted=include_deleted,
    )
    items, next_cursor = await TodoService(session).list_todos(filters, sort_spec, cursor, limit)
    names = await UserRepository(session).names_for([t.updated_by_id for t in items])
    return TodoPage(
        items=[
            TodoRead.from_todo(t, updated_by=names.get(t.updated_by_id)) for t in items
        ],
        next_cursor=next_cursor,
    )


@router.get(
    "/{todo_id}",
    response_model=TodoRead,
    summary="Get a single todo",
    response_description="The todo with its ``depends_on`` list populated.",
    responses={
        404: {"description": "Todo not found (or soft-deleted, unless ``include_deleted`` is set)."}
    },
)
async def get_todo(
    todo_id: UUID,
    response: Response = None,  # FastAPI injects this
    include_deleted: bool = Query(default=False, description=_DELETED_DOC),
    session: AsyncSession = Depends(get_session),
) -> TodoRead:
    todo = await TodoService(session).get(todo_id, include_deleted=include_deleted)
    edges = await DependencyRepository(session).list_for(todo_id)

    # One lookup covering the todo's own author and every edge author, for the
    # same reason the list endpoint batches them: a page of dependencies must
    # not become a page of queries.
    names = await UserRepository(session).names_for(
        [todo.updated_by_id, *(e.created_by_id for e in edges)]
    )
    depends_on = [
        DependencyRead(
            id=e.depends_on_id,
            added_by=names.get(e.created_by_id),
            added_at=e.created_at,
        )
        for e in edges
    ]
    return _with_etag(response, todo, names.get(todo.updated_by_id), depends_on)


@router.patch(
    "/{todo_id}",
    response_model=TodoRead,
    summary="Update a todo",
    response_description="The updated todo. ``version`` is incremented.",
    responses={
        409: {"description": "Version conflict — someone else wrote first. Body carries ``current``."},
        428: {"description": "Missing ``If-Match`` header."},
    },
)
async def update_todo(
    todo_id: UUID,
    payload: TodoUpdate = None,  # FastAPI injects from body
    response: Response = None,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> TodoRead:
    todo = await TodoService(session, user.id).update(todo_id, expected_version, payload)
    publish_todo_change("updated", todo, user.username)
    return _with_etag(response, todo, user.username)


@router.delete(
    "/{todo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a todo",
    response_description="No body on success. The todo is hidden, not destroyed.",
    responses={
        409: {"description": "Version conflict."},
        428: {"description": "Missing ``If-Match`` header."},
    },
)
async def delete_todo(
    todo_id: UUID,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    deleted = await TodoService(session, user.id).delete(todo_id, expected_version)
    publish_todo_change("deleted", deleted, user.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{todo_id}/restore",
    response_model=TodoRead,
    summary="Restore a soft-deleted todo",
    response_description="The restored todo, live again.",
    responses={
        404: {"description": "Todo not found or not deleted."},
        409: {"description": "Version conflict."},
    },
)
async def restore_todo(
    todo_id: UUID,
    response: Response = None,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> TodoRead:
    todo = await TodoService(session, user.id).restore(todo_id, expected_version)
    publish_todo_change("restored", todo, user.username)
    return _with_etag(response, todo, user.username)


@router.post(
    "/{todo_id}/status",
    response_model=StatusChangeResult,
    summary="Change a todo's status",
    description=(
        "Transitions a todo through its lifecycle. "
        "Moving to ``in_progress`` or ``completed`` requires every dependency "
        "to be complete.  Completing a recurring todo automatically spawns the "
        "next occurrence — the response carries it in ``next_occurrence``."
    ),
    response_description="The updated todo, and the spawned occurrence if this was a recurring completion.",
    responses={
        409: {"description": "Version conflict."},
        422: {"description": "Blocked by incomplete dependencies, or invalid transition."},
        428: {"description": "Missing ``If-Match`` header."},
    },
)
async def change_status(
    todo_id: UUID,
    payload: StatusChange = None,
    response: Response = None,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> StatusChangeResult:
    todo, spawned = await StatusService(session, user.id).change_status(
        todo_id, expected_version, payload.status
    )
    publish_todo_change("status_changed", todo, user.username, status=todo.status.value)
    if spawned is not None:
        # A recurring completion creates a second row. Without its own event
        # the other tabs would refetch and find a todo nobody announced.
        publish_todo_change("created", spawned, user.username)
    _set_etag(response, todo)
    return StatusChangeResult(
        todo=TodoRead.from_todo(todo, updated_by=user.username),
        next_occurrence=(
            TodoRead.from_todo(spawned, updated_by=user.username) if spawned else None
        ),
    )
