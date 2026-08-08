"""The body a 409 carries, in one place.

Both TodoService and StatusService raise VersionConflict, and both must attach
the same `current` state — byte for byte, or the client has two shapes to
handle for one situation. This lived as an identical private method on each.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.todo import Todo
from app.repositories.user_repo import UserRepository
from app.schemas.todo import TodoRead


async def conflict_payload(session: AsyncSession, todo: Todo) -> dict:
    """The 409 body names whoever actually made the change.

    Without resolving the username here the banner falls back to "someone
    else" — which defeats the point of recording an author at all.
    """
    names = await UserRepository(session).names_for([todo.updated_by_id])
    return TodoRead.from_todo(todo, updated_by=names.get(todo.updated_by_id)).model_dump(
        mode="json"
    )
