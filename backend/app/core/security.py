"""Password hashing and JWT issuing/verification.

Kept free of FastAPI and SQLAlchemy so it can be unit-tested directly.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    """bcrypt with a per-password salt. The cost factor is bcrypt's default (12)."""
    # bcrypt silently truncates at 72 bytes; reject rather than accept a
    # password whose tail is ignored.
    encoded = plain.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be at most 72 bytes.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash in the database — treat as a failed login rather than
        # a 500, but never as a success.
        return False


def create_access_token(user_id: UUID, username: str) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError (expired, bad signature, malformed) on any failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
