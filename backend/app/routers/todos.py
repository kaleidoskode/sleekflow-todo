from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import MalformedPrecondition, PreconditionRequired
from app.schemas.todo import TodoCreate, TodoRead, TodoUpdate
from app.services.todo_service import TodoService

router = APIRouter(prefix="/api/todos", tags=["todos"])


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


def _with_etag(response: Response, todo) -> TodoRead:
    response.headers["ETag"] = f'"{todo.version}"'
    return TodoRead.from_todo(todo)


@router.post("", response_model=TodoRead, status_code=status.HTTP_201_CREATED)
async def create_todo(
    payload: TodoCreate, response: Response, session: AsyncSession = Depends(get_session)
) -> TodoRead:
    return _with_etag(response, await TodoService(session).create(payload))


@router.get("/{todo_id}", response_model=TodoRead)
async def get_todo(
    todo_id: UUID,
    response: Response,
    include_deleted: bool = False,
    session: AsyncSession = Depends(get_session),
) -> TodoRead:
    todo = await TodoService(session).get(todo_id, include_deleted=include_deleted)
    return _with_etag(response, todo)


@router.patch("/{todo_id}", response_model=TodoRead)
async def update_todo(
    todo_id: UUID,
    payload: TodoUpdate,
    response: Response,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
) -> TodoRead:
    todo = await TodoService(session).update(todo_id, expected_version, payload)
    return _with_etag(response, todo)


@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(
    todo_id: UUID,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await TodoService(session).delete(todo_id, expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{todo_id}/restore", response_model=TodoRead)
async def restore_todo(
    todo_id: UUID,
    response: Response,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
) -> TodoRead:
    return _with_etag(response, await TodoService(session).restore(todo_id, expected_version))
