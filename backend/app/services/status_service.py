from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import Status
from app.domain.recurrence import next_occurrence
from app.domain.transitions import validate_transition
from app.errors import NotFound, VersionConflict
from app.models.todo import Todo
from app.repositories.dependency_repo import DependencyRepository
from app.repositories.todo_repo import TodoRepository
from app.schemas.todo import TodoRead


class StatusService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.todos = TodoRepository(session)
        self.deps = DependencyRepository(session)

    async def change_status(
        self, todo_id: UUID, expected_version: int, target: Status
    ) -> tuple[Todo, Todo | None]:
        current = await self.todos.get(todo_id)
        if current is None:
            raise NotFound(f"No todo with id {todo_id}.")

        validate_transition(current.status, target, current.unmet_dependency_count)

        values: dict = {"status": target}
        values["completed_at"] = datetime.now(UTC) if target is Status.COMPLETED else None

        updated = await self.todos.update_versioned(todo_id, expected_version, values)
        if updated is None:
            # Someone else moved first — the compare-and-set is also what stops
            # two concurrent completions both spawning an occurrence.
            fresh = await self.todos.get(todo_id)
            if fresh is None:
                raise NotFound(f"No todo with id {todo_id}.")
            raise VersionConflict(
                "This todo was modified by someone else. Reload and retry.",
                extra={"current": TodoRead.from_todo(fresh).model_dump(mode="json")},
            )

        spawned = None
        if target is Status.COMPLETED and updated.recurrence_unit is not None:
            spawned = await self._spawn_next(updated)

        # Completing or reopening changes whether this todo satisfies its dependents.
        await self.deps.recompute_counts(await self.deps.dependents_of(todo_id))
        await self.session.commit()
        return updated, spawned

    async def _spawn_next(self, completed: Todo) -> Todo:
        anchor = completed.recurrence_anchor_due or completed.due_date
        due, index = next_occurrence(
            anchor=anchor,
            unit=completed.recurrence_unit,
            interval=completed.recurrence_interval,
            current_index=completed.occurrence_index,
            now=datetime.now(UTC),
        )
        # Dependencies are deliberately not copied (spec 2.7).
        return await self.todos.insert(
            Todo(
                name=completed.name,
                description=completed.description,
                due_date=due,
                status=Status.NOT_STARTED,
                priority=completed.priority,
                recurrence_unit=completed.recurrence_unit,
                recurrence_interval=completed.recurrence_interval,
                recurrence_series_id=completed.recurrence_series_id,
                recurrence_anchor_due=anchor,
                occurrence_index=index,
            )
        )
