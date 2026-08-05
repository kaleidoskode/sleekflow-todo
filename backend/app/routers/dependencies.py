from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.services.dependency_service import DependencyService

router = APIRouter(prefix="/api/todos", tags=["dependencies"])


class DependencyCreate(BaseModel):
    depends_on_id: UUID


@router.post("/{todo_id}/dependencies", status_code=status.HTTP_201_CREATED)
async def add_dependency(
    todo_id: UUID, payload: DependencyCreate, session: AsyncSession = Depends(get_session)
) -> Response:
    await DependencyService(session).add_dependency(todo_id, payload.depends_on_id)
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete("/{todo_id}/dependencies/{depends_on_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_dependency(
    todo_id: UUID, depends_on_id: UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await DependencyService(session).remove_dependency(todo_id, depends_on_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
