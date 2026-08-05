from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.enums import Status
from app.errors import MalformedPrecondition, PreconditionRequired
from app.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SortSpec
from app.repositories.todo_repo import TodoFilter
from app.schemas.todo import NAME_TO_PRIORITY, TodoCreate, TodoPage, TodoRead, TodoUpdate
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


@router.get("", response_model=TodoPage)
async def list_todos(
    status_filter: list[Status] = Query(default=[], alias="status"),
    priority: list[str] = Query(default=[]),
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    blocked: bool | None = None,
    include_deleted: bool = False,
    sort: str = "due_date",
    cursor: str | None = None,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
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
    return TodoPage(items=[TodoRead.from_todo(t) for t in items], next_cursor=next_cursor)


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
