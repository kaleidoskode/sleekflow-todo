from typing import Any

from pydantic import BaseModel


class ProblemDetail(BaseModel):
    """RFC 9457 Problem Details."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    errors: list[dict[str, Any]] | None = None
