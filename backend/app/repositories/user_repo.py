from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def names_for(self, user_ids: list[UUID | None]) -> dict[UUID, str]:
        """Resolve a batch of ids to usernames in one query.

        The list endpoint calls this once per page rather than once per row —
        a page of 50 todos would otherwise fire 50 lookups just to render an
        attribution line.
        """
        wanted = {uid for uid in user_ids if uid is not None}
        if not wanted:
            return {}

        rows = await self.session.execute(
            select(User.id, User.username).where(User.id.in_(wanted))
        )
        return {row.id: row.username for row in rows}
