from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidRecurrence, NotFound, VersionConflict
from app.core.pagination import SortSpec
from app.models.todo import Todo
from app.repositories.dependency_repo import DependencyRepository
from app.repositories.todo_repo import TodoFilter, TodoRepository
from app.schemas.todo import NAME_TO_PRIORITY, TodoCreate, TodoRead, TodoUpdate


class TodoService:
    def __init__(self, session: AsyncSession, actor_id: UUID | None = None) -> None:
        self.session = session
        self.actor_id = actor_id
        self.repo = TodoRepository(session)

    async def create(self, payload: TodoCreate) -> Todo:
        todo = Todo(
            name=payload.name,
            description=payload.description,
            due_date=payload.due_date,
            priority=int(NAME_TO_PRIORITY[payload.priority]),
            recurrence_unit=payload.recurrence_unit,
            recurrence_interval=payload.recurrence_interval,
            created_by_id=self.actor_id,
            updated_by_id=self.actor_id,
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
            values["priority"] = int(NAME_TO_PRIORITY[values["priority"]])

        if {"recurrence_unit", "recurrence_interval", "due_date"} & values.keys():
            current = await self._check_merged_recurrence(todo_id, values)
            merged_unit = values.get("recurrence_unit", current.recurrence_unit)
            if merged_unit is not None and current.recurrence_series_id is None:
                # PATCH just turned a plain todo into a recurring one — seed the
                # series the same way `create` does. Never reseed a todo that is
                # already part of a series.
                values["recurrence_series_id"] = uuid4()
                values["recurrence_anchor_due"] = values.get("due_date", current.due_date)
                values["occurrence_index"] = 0

        values["updated_by_id"] = self.actor_id
        updated = await self.repo.update_versioned(todo_id, expected_version, values)
        if updated is None:
            await self._raise_conflict_or_not_found(todo_id)
        await self.session.commit()
        return updated

    async def delete(self, todo_id: UUID, expected_version: int) -> Todo:
        deleted = await self.repo.soft_delete(todo_id, expected_version, self.actor_id)
        if deleted is None:
            await self._raise_conflict_or_not_found(todo_id)
        deps = DependencyRepository(self.session)
        await deps.recompute_counts(await deps.dependents_of(todo_id))
        await self.session.commit()
        return deleted

    async def list_todos(
        self, filters: TodoFilter, sort: SortSpec, cursor: str | None, limit: int
    ) -> tuple[list[Todo], str | None]:
        return await self.repo.list_page(filters, sort, cursor, limit)

    async def restore(self, todo_id: UUID, expected_version: int) -> Todo:
        restored = await self.repo.restore(todo_id, expected_version, self.actor_id)
        if restored is None:
            current = await self.repo.get(todo_id, include_deleted=True)
            if current is None:
                raise NotFound(f"No todo with id {todo_id}.")
            if current.deleted_at is None:
                raise NotFound(f"Todo {todo_id} is not deleted.")
            raise VersionConflict(
                "This todo was modified by someone else. Reload and retry.",
                extra={"current": TodoRead.from_todo(current).model_dump(mode="json")},
            )
        deps = DependencyRepository(self.session)
        await deps.recompute_counts(await deps.dependents_of(todo_id))
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

    async def _check_merged_recurrence(self, todo_id: UUID, values: dict) -> Todo:
        """Recurrence is a pair, and a PATCH may set only one half.

        This read is for validation only. The compare-and-set that follows is
        still the single statement guaranteeing atomicity, and it re-checks the
        version — so a row changing between this read and the write yields 409,
        not a corrupt update. Returns `current` so the caller can also decide
        whether this PATCH newly turns the todo recurring, without a second read.
        """
        current = await self.repo.get(todo_id)
        if current is None:
            raise NotFound(f"No todo with id {todo_id}.")

        unit = values.get("recurrence_unit", current.recurrence_unit)
        interval = values.get("recurrence_interval", current.recurrence_interval)
        due = values.get("due_date", current.due_date)

        if (unit is None) != (interval is None):
            raise InvalidRecurrence(
                "Choose how often it repeats and how many units between each one."
            )
        if unit is not None and due is None:
            raise InvalidRecurrence(
                "Pick a due date first: a repeating todo counts from it."
            )
        return current
