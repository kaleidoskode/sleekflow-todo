from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DependencyCycle, NotFound
from app.repositories.dependency_repo import DependencyRepository
from app.repositories.todo_repo import TodoRepository


class DependencyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.deps = DependencyRepository(session)
        self.todos = TodoRepository(session)

    async def add_dependency(self, todo_id: UUID, depends_on_id: UUID) -> None:
        for candidate in (todo_id, depends_on_id):
            if await self.todos.get(candidate) is None:
                raise NotFound(f"No todo with id {candidate}.")

        cycle = await self.deps.find_cycle_path(todo_id, depends_on_id)
        if cycle is not None:
            raise DependencyCycle(
                "This dependency would create a cycle.",
                extra={"cycle_path": [str(i) for i in cycle]},
            )

        await self.deps.add(todo_id, depends_on_id)
        await self.deps.recompute_counts([todo_id])
        await self.session.commit()

    async def remove_dependency(self, todo_id: UUID, depends_on_id: UUID) -> None:
        if not await self.deps.remove(todo_id, depends_on_id):
            raise NotFound(f"Todo {todo_id} does not depend on {depends_on_id}.")
        await self.deps.recompute_counts([todo_id])
        await self.session.commit()
