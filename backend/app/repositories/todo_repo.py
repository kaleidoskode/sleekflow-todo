from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.todo import Todo


class TodoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, todo_id: UUID, *, include_deleted: bool = False) -> Todo | None:
        stmt = select(Todo).where(Todo.id == todo_id)
        if not include_deleted:
            stmt = stmt.where(Todo.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert(self, todo: Todo) -> Todo:
        self.session.add(todo)
        await self.session.flush()
        await self.session.refresh(todo)
        return todo

    async def update_versioned(
        self, todo_id: UUID, expected_version: int, values: dict[str, Any]
    ) -> Todo | None:
        """Single-statement compare-and-set. None means lost race or row gone."""
        stmt = (
            update(Todo)
            .where(
                Todo.id == todo_id,
                Todo.version == expected_version,
                Todo.deleted_at.is_(None),
            )
            .values(**values, version=Todo.version + 1, updated_at=datetime.now(UTC))
            .returning(Todo)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def soft_delete(self, todo_id: UUID, expected_version: int) -> Todo | None:
        return await self.update_versioned(todo_id, expected_version, {"deleted_at": datetime.now(UTC)})

    async def restore(self, todo_id: UUID, expected_version: int) -> Todo | None:
        stmt = (
            update(Todo)
            .where(
                Todo.id == todo_id,
                Todo.version == expected_version,
                Todo.deleted_at.is_not(None),
            )
            .values(deleted_at=None, version=Todo.version + 1, updated_at=datetime.now(UTC))
            .returning(Todo)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
