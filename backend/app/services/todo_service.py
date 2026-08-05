from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound, VersionConflict
from app.models.todo import Todo
from app.repositories.todo_repo import TodoRepository
from app.schemas.todo import NAME_TO_PRIORITY, TodoCreate, TodoRead, TodoUpdate


class TodoService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TodoRepository(session)

    async def create(self, payload: TodoCreate) -> Todo:
        todo = Todo(
            name=payload.name,
            description=payload.description,
            due_date=payload.due_date,
            priority=int(NAME_TO_PRIORITY[payload.priority]),
            recurrence_unit=payload.recurrence_unit,
            recurrence_interval=payload.recurrence_interval,
        )
        if payload.recurrence_unit is not None:
            todo.recurrence_series_id = uuid4()
            todo.recurrence_anchor_due = payload.due_date
            todo.occurrence_index = 0
        todo = await self.repo.insert(todo)
        await self.session.commit()
        return todo

    async def get(self, todo_id: UUID, *, include_deleted: bool = False) -> Todo:
        todo = await self.repo.get(todo_id, include_deleted=include_deleted)
        if todo is None:
            raise NotFound(f"No todo with id {todo_id}.")
        return todo

    async def update(self, todo_id: UUID, expected_version: int, payload: TodoUpdate) -> Todo:
        values = payload.model_dump(exclude_unset=True)
        if "priority" in values:
            if values["priority"] not in NAME_TO_PRIORITY:
                raise ValueError("priority must be one of: low, medium, high")
            values["priority"] = int(NAME_TO_PRIORITY[values["priority"]])

        updated = await self.repo.update_versioned(todo_id, expected_version, values)
        if updated is None:
            await self._raise_conflict_or_not_found(todo_id)
        await self.session.commit()
        return updated

    async def delete(self, todo_id: UUID, expected_version: int) -> Todo:
        deleted = await self.repo.soft_delete(todo_id, expected_version)
        if deleted is None:
            await self._raise_conflict_or_not_found(todo_id)
        await self.session.commit()
        return deleted

    async def restore(self, todo_id: UUID) -> Todo:
        restored = await self.repo.restore(todo_id)
        if restored is None:
            raise NotFound(f"No deleted todo with id {todo_id}.")
        await self.session.commit()
        return restored

    async def _raise_conflict_or_not_found(self, todo_id: UUID) -> None:
        """Distinguish 'someone else wrote first' from 'it is not there'."""
        current = await self.repo.get(todo_id)
        if current is None:
            raise NotFound(f"No todo with id {todo_id}.")
        raise VersionConflict(
            "This todo was modified by someone else. Reload and retry.",
            extra={"current": TodoRead.from_todo(current).model_dump(mode="json")},
        )
