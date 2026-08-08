from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dependency import TodoDependency

CYCLE_PROBE = text("""
WITH RECURSIVE reachable(id, path) AS (
    SELECT depends_on_id, ARRAY[todo_id, depends_on_id]
    FROM todo_dependencies
    WHERE todo_id = :start
  UNION ALL
    SELECT d.depends_on_id, r.path || d.depends_on_id
    FROM todo_dependencies d
    JOIN reachable r ON d.todo_id = r.id
    WHERE NOT d.depends_on_id = ANY(r.path)
)
SELECT path FROM reachable WHERE id = :target LIMIT 1
""")

RECOMPUTE_COUNTS = text("""
UPDATE todos t
SET unmet_dependency_count = (
    SELECT count(*)
    FROM todo_dependencies d
    JOIN todos dep ON dep.id = d.depends_on_id
    WHERE d.todo_id = t.id
      AND dep.status <> 'completed'
      AND dep.deleted_at IS NULL
)
WHERE t.id = ANY(:ids)
""")


class DependencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(
        self, todo_id: UUID, depends_on_id: UUID, actor_id: UUID | None = None
    ) -> None:
        stmt = (
            pg_insert(TodoDependency)
            .values(todo_id=todo_id, depends_on_id=depends_on_id, created_by_id=actor_id)
            # Re-adding an existing edge keeps the original author. The first
            # person to draw the link is the one who blocked the todo; a second
            # request that changes nothing should not take credit for it.
            .on_conflict_do_nothing()
        )
        await self.session.execute(stmt)

    async def remove(self, todo_id: UUID, depends_on_id: UUID) -> bool:
        result = await self.session.execute(
            delete(TodoDependency).where(
                TodoDependency.todo_id == todo_id,
                TodoDependency.depends_on_id == depends_on_id,
            )
        )
        return result.rowcount > 0

    async def list_for(self, todo_id: UUID) -> list[TodoDependency]:
        """The full edge rows, so callers can show who drew each link.

        Ordered oldest first: the list is a history of how the todo got
        blocked, and an unordered one reshuffles itself between reads.
        """
        stmt = (
            select(TodoDependency)
            .where(TodoDependency.todo_id == todo_id)
            .order_by(TodoDependency.created_at, TodoDependency.depends_on_id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def dependents_of(self, todo_id: UUID) -> list[UUID]:
        stmt = select(TodoDependency.todo_id).where(TodoDependency.depends_on_id == todo_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_cycle_path(self, todo_id: UUID, depends_on_id: UUID) -> list[UUID] | None:
        """Would `todo_id -> depends_on_id` close a loop? Returns the path if so."""
        if todo_id == depends_on_id:
            return [todo_id, depends_on_id]
        result = await self.session.execute(
            CYCLE_PROBE, {"start": depends_on_id, "target": todo_id}
        )
        row = result.first()
        return list(row[0]) if row else None

    async def recompute_counts(self, todo_ids: list[UUID]) -> None:
        """Refresh derived state. Deliberately does not touch `version`."""
        if todo_ids:
            await self.session.execute(RECOMPUTE_COUNTS, {"ids": todo_ids})
