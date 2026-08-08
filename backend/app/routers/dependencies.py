"""Add and remove dependency edges between todos.

A cycle is rejected before it reaches the database — the error body carries
the offending ``cycle_path`` so the caller can see which link would close
the loop.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import current_user
from app.core.events import publish_dependency_change
from app.models.user import User
from app.services.dependency_service import DependencyService

router = APIRouter(
    tags=["dependencies"],
    dependencies=[Depends(current_user)],
)


class DependencyCreate(BaseModel):
    """The todo this one should wait on."""

    depends_on_id: UUID = Field(
        description="UUID of the todo that must be completed first.",
        examples=["018f3b2c-0000-7000-8000-000000000042"],
    )


@router.post(
    "/api/todos/{todo_id}/dependencies",
    status_code=status.HTTP_201_CREATED,
    summary="Add a dependency",
    response_description="No body on success. The dependent's ``unmet_dependency_count`` is incremented.",
    responses={
        422: {"description": "This edge would create a cycle. Body carries ``cycle_path``."},
        404: {"description": "Either todo does not exist."},
    },
)
async def add_dependency(
    todo_id: UUID,
    payload: DependencyCreate = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    await DependencyService(session, user.id).add_dependency(todo_id, payload.depends_on_id)
    # An edge change flips is_blocked on the dependent, so other tabs must
    # re-read even though no todo row was directly edited here.
    publish_dependency_change("dependency_added", todo_id, user.username)
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete(
    "/api/todos/{todo_id}/dependencies/{depends_on_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a dependency",
    response_description="No body on success. The count is recomputed.",
    responses={404: {"description": "The dependency edge does not exist."}},
)
async def remove_dependency(
    todo_id: UUID,
    depends_on_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    await DependencyService(session, user.id).remove_dependency(todo_id, depends_on_id)
    publish_dependency_change("dependency_removed", todo_id, user.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
