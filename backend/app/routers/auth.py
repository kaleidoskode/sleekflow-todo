"""Register, sign in, and identify the current user.

The TODO list itself is shared — an account gates access and names the actor,
it does not own any todos.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import User
from app.schemas.auth import Credentials, TokenResponse, UserRead
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=AuthService.issue_token(user),
        expires_in=settings.access_token_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    response_description="A bearer token, so registering signs you straight in.",
    responses={409: {"description": "That username is already registered."}},
)
async def register(
    payload: Credentials, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    user = await AuthService(session).register(payload.username, payload.password)
    return _token_response(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Sign in",
    response_description="A bearer token to send as `Authorization: Bearer <token>`.",
    responses={401: {"description": "Username or password is incorrect."}},
)
async def login(
    payload: Credentials, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    user = await AuthService(session).authenticate(payload.username, payload.password)
    return _token_response(user)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Who am I",
    description="Confirms a token is still valid and returns the account it belongs to.",
    responses={401: {"description": "Missing, malformed, or expired token."}},
)
async def me(user: User = Depends(current_user)) -> UserRead:
    return UserRead.model_validate(user)
