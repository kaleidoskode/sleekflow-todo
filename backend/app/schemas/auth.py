from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Credentials(BaseModel):
    """Payload for both register and login."""

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="3-50 characters: letters, digits, underscore, dot or hyphen.",
        examples=["ada"],
    )
    password: str = Field(
        min_length=8,
        max_length=72,
        description="At least 8 characters. bcrypt ignores anything past 72 bytes, so that is the cap.",
        examples=["correct-horse-battery"],
    )


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
