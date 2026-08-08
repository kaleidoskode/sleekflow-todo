"""Shared FastAPI dependencies."""

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import Unauthenticated
from app.models.user import User
from app.services.auth_service import AuthService

# Declared as a security scheme so the gate is visible in the OpenAPI document,
# not just enforced at runtime. Reading the header off the raw Request works,
# but FastAPI cannot see into it: Swagger then shows no Authorize button and no
# padlocks, so protected routes look open and "Try it out" can only ever return
# 401. auto_error=False keeps the 401 body ours — FastAPI's own would be a bare
# {"detail": ...} instead of the Problem Details every other error uses.
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="Bearer token",
    description="The `access_token` from `/api/auth/login` or `/api/auth/register`.",
)


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolves the bearer token to a user, or raises 401.

    The list itself is shared, so this gates access rather than scoping data —
    every signed-in user sees the same board.
    """
    if credentials is None:
        # HTTPBearer collapses "no header at all", "not a bearer scheme" and
        # "bearer with an empty token" into None. The raw header tells the
        # first apart from the rest, which is the difference between "you are
        # signed out" and "your client is sending the header wrong".
        if request.headers.get("authorization") is None:
            raise Unauthenticated("Sign in to use the API.")
        raise Unauthenticated("Authorization header must be 'Bearer <token>'.")

    return await AuthService(session).user_from_token(credentials.credentials)
