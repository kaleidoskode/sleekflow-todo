"""Shared FastAPI dependencies."""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import Unauthenticated
from app.models.user import User
from app.services.auth_service import AuthService


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Resolves the bearer token to a user, or raises 401.

    The list itself is shared, so this gates access rather than scoping data —
    every signed-in user sees the same board.
    """
    header = request.headers.get("authorization")
    if header is None:
        raise Unauthenticated("Sign in to use the API.")

    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or token == "":
        raise Unauthenticated("Authorization header must be 'Bearer <token>'.")

    return await AuthService(session).user_from_token(token)
