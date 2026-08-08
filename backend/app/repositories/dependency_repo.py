from collections import defaultdict, deque
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dependency import TodoDependency

# Decides whether `:target` is reachable from `:start`. UNION, not UNION ALL:
# the working set is deduplicated by node, so each node is expanded once and the
# walk is O(V+E). It also terminates on cyclic data without a path guard, since
# a node already produced is never produced again.
#
# The previous version carried the path along each branch under UNION ALL, which
# enumerates every distinct *path* rather than every node — exponential in a wide
# graph. Measured on a 3-wide layered graph: 36 nodes / 99 edges took 166 ms and
# 42 nodes / 117 edges took 2.0 s, growing ~3x per layer. Around fifty nodes it
# stops returning. This runs on every dependency add.
CYCLE_EXISTS = text("""
WITH RECURSIVE reachable(id) AS (
    SELECT depends_on_id
    FROM todo_dependencies
    WHERE todo_id = :start
  UNION
    SELECT d.depends_on_id
    FROM todo_dependencies d
    JOIN reachable r ON d.todo_id = r.id
)
SELECT 1 FROM reachable WHERE id = :target LIMIT 1
""")

# Only runs once a cycle is known to exist, purely to name it in the error body.
# Returns the edges of the subgraph reachable from `:start` — bounded by the
# graph, not by the number of paths through it — which a breadth-first walk in
# Python turns into the shortest offending path.
REACHABLE_EDGES = text("""
WITH RECURSIVE reachable(id) AS (
    SELECT depends_on_id
    FROM todo_dependencies
    WHERE todo_id = :start
  UNION
    SELECT d.depends_on_id
    FROM todo_dependencies d
    JOIN reachable r ON d.todo_id = r.id
)
SELECT d.todo_id, d.depends_on_id
FROM todo_dependencies d
WHERE d.todo_id = :start OR d.todo_id IN (SELECT id FROM reachable)
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


def _shortest_path(
    edges: list[tuple[UUID, UUID]], start: UUID, target: UUID
) -> list[UUID] | None:
    """Breadth-first, so the reported cycle is the shortest one, not an arbitrary
    walk. Visits each node once — the expense the old query paid was in
    enumerating paths, and there is no reason to pay it to render a message."""
    adjacency: dict[UUID, list[UUID]] = defaultdict(list)
    for source, destination in edges:
        adjacency[source].append(destination)

    came_from: dict[UUID, UUID | None] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for neighbour in adjacency.get(node, ()):
            if neighbour not in came_from:
                came_from[neighbour] = node
                queue.append(neighbour)

    if target not in came_from:
        return None

    path: list[UUID] = []
    cursor: UUID | None = target
    while cursor is not None:
        path.append(cursor)
        cursor = came_from[cursor]
    return list(reversed(path))


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
        """Would `todo_id -> depends_on_id` close a loop? Returns the path if so.

        Two steps on purpose. The *decision* is a node-reachability walk that
        visits each node once; building a readable path is a second query that
        only runs when the answer is already known to be yes. Doing both at once
        means carrying a path down every branch, which is exponential in a wide
        graph — and this is on the hot path of every dependency add.
        """
        if todo_id == depends_on_id:
            return [todo_id, depends_on_id]

        exists = await self.session.execute(
            CYCLE_EXISTS, {"start": depends_on_id, "target": todo_id}
        )
        if exists.first() is None:
            return None

        edges = await self.session.execute(REACHABLE_EDGES, {"start": depends_on_id})
        return _shortest_path(edges.all(), depends_on_id, todo_id) or [
            todo_id,
            depends_on_id,
        ]

    async def recompute_counts(self, todo_ids: list[UUID]) -> None:
        """Refresh derived state. Deliberately does not touch `version`."""
        if todo_ids:
            await self.session.execute(RECOMPUTE_COUNTS, {"ids": todo_ids})
