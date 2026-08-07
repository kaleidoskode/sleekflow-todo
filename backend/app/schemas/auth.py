import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class Credentials(BaseModel):
    """Payload for both register and login.

    The length and shape rules are enforced by validators rather than by
    `Field` constraints so the messages are written for the person filling in
    the form. Pydantic's generated text ("String should have at least 8
    characters") names a type, not a thing anyone recognises.
    """

    username: str = Field(
        description="3-50 characters: letters, digits, underscore, dot or hyphen.",
        examples=["ada"],
    )
    password: str = Field(
        description="At least 8 characters. bcrypt ignores anything past 72 bytes, so that is the cap.",
        examples=["correct-horse-battery"],
    )

    @field_validator("username")
    @classmethod
    def check_username(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Username must be at least 3 characters.")
        if len(value) > 50:
            raise ValueError("Username must be 50 characters or fewer.")
        if not _USERNAME_RE.match(value):
            raise ValueError(
                "Username can use letters, digits, underscores, dots and hyphens, but no spaces."
            )
        return value

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        # bcrypt silently ignores anything past 72 bytes, so a longer password
        # would be accepted while part of it did nothing.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password is too long. 72 bytes is the maximum.")
        return value


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str = Field(examples=["ada"])
    created_at: datetime


class TokenResponse(BaseModel):
    """Returned by register and login."""

    access_token: str = Field(description="JWT bearer token. Send as `Authorization: Bearer <token>`.")
    token_type: str = Field(default="bearer", examples=["bearer"])
    expires_in: int = Field(description="Token lifetime in seconds.", examples=[43200])
    user: UserRead
