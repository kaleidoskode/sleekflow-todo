from uuid import UUID

import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidCredentials, Unauthenticated, UsernameTaken
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _by_username(self, username: str) -> User | None:
        # Case-insensitive: "Ada" and "ada" are the same account.
        stmt = select(User).where(func.lower(User.username) == username.lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def register(self, username: str, password: str) -> User:
        if await self._by_username(username) is not None:
            raise UsernameTaken(f"The username {username!r} is already taken.")

        user = User(username=username, password_hash=hash_password(password))
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        await self.session.commit()
        return user

    async def authenticate(self, username: str, password: str) -> User:
        user = await self._by_username(username)

        # Same error and roughly the same work whether the user exists or the
        # password is wrong, so the response cannot be used to enumerate
        # accounts.
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise InvalidCredentials("Username or password is incorrect.")
        if not verify_password(password, user.password_hash):
            raise InvalidCredentials("Username or password is incorrect.")
        return user

    async def user_from_token(self, token: str) -> User:
        try:
            payload = decode_access_token(token)
            user_id = UUID(payload["sub"])
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise Unauthenticated("Sign in again — your session is invalid or expired.") from exc

        user = (
            await self.session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            # Valid signature, but the account is gone.
            raise Unauthenticated("Sign in again — your session is invalid or expired.")
        return user

    @staticmethod
    def issue_token(user: User) -> str:
        return create_access_token(user.id, user.username)


# A real bcrypt hash of a value nobody will submit, used only to keep the
# timing of "no such user" close to "wrong password".
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeO3S0kKvFOxQr3hNfMcMbLOJcnHu4Vqre"
