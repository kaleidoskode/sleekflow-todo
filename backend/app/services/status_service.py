from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import Status
from app.domain.recurrence import next_occurrence
from app.domain.transitions import DEPENDENCY_GUARDED_TARGETS, validate_transition
from app.errors import BlockedByDependencies, NotFound, VersionConflict
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

        if current.version != expected_version:
            # A stale caller must get `current` back so it can recover its version —
            # this is the same 409 contract every other mutating endpoint gives,
            # and it must fire before the same-status / dependency checks below,
            # which are about intent, not staleness.
            raise VersionConflict(
                "This todo was modified by someone else. Reload and retry.",
                extra={"current": TodoRead.from_todo(current).model_dump(mode="json")},
            )

        validate_transition(current.status, target, current.unmet_dependency_count)

        values: dict = {"status": target}
        values["completed_at"] = datetime.now(UTC) if target is Status.COMPLETED else None

        # The dependency-count column is deliberately updated without bumping
        # `version` (Task 9), so the plain version check above cannot detect a
        # concurrent re-block. Fold the guard into the CAS itself for the targets
        # that require it, so "still blocked" and "someone else moved first" are
        # both caught atomically rather than trusting the count read above.
        require_unblocked = target in DEPENDENCY_GUARDED_TARGETS
        updated = await self.todos.update_versioned(
            todo_id, expected_version, values, require_unblocked=require_unblocked
        )
        if updated is None:
            # Someone else moved first, or (for guarded targets) a dependency was
            # re-blocked between our read and the write — distinguish by re-reading.
            fresh = await self.todos.get(todo_id)
            if fresh is None:
                raise NotFound(f"No todo with id {todo_id}.")
            if fresh.version != expected_version:
                raise VersionConflict(
                    "This todo was modified by someone else. Reload and retry.",
                    extra={"current": TodoRead.from_todo(fresh).model_dump(mode="json")},
                )
            raise BlockedByDependencies(
                f"Cannot move to '{target}' while {fresh.unmet_dependency_count} "
                f"dependenc{'y is' if fresh.unmet_dependency_count == 1 else 'ies are'} incomplete.",
                extra={"unmet_dependency_count": fresh.unmet_dependency_count},
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
