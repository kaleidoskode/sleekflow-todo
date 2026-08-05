"""Keyset pagination cursors. Pure — no database, no HTTP."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class SortField(StrEnum):
    DUE_DATE = "due_date"
    PRIORITY = "priority"
    STATUS = "status"
    NAME = "name"


@dataclass(frozen=True)
class SortSpec:
    field: SortField
    descending: bool

    @classmethod
    def parse(cls, raw: str) -> "SortSpec":
        descending = raw.startswith("-")
        name = raw[1:] if descending else raw
        try:
            field = SortField(name)
        except ValueError as exc:
            allowed = ", ".join(f.value for f in SortField)
            raise ValueError(f"Unknown sort field '{name}'. Allowed: {allowed}.") from exc
        return cls(field=field, descending=descending)


def encode_cursor(sort_value: Any, todo_id: UUID) -> str:
    if isinstance(sort_value, datetime):
        payload = {"t": "dt", "v": sort_value.isoformat(), "id": str(todo_id)}
    elif isinstance(sort_value, int):
        payload = {"t": "int", "v": sort_value, "id": str(todo_id)}
    else:
        payload = {"t": "str", "v": str(sort_value), "id": str(todo_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, UUID]:
    """Every failure mode must surface as ValueError.

    Type reconstruction stays INSIDE the try: a cursor that base64- and
    JSON-decodes cleanly can still carry a mismatched value type, and
    `int(None)` raises TypeError. Cursors arrive as query parameters, so an
    unhandled TypeError there is a 500 from user input.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        kind, value = payload["t"], payload["v"]
        todo_id = UUID(payload["id"])
        if kind == "dt":
            return datetime.fromisoformat(value), todo_id
        if kind == "int":
            return int(value), todo_id
        if kind == "str":
            return str(value), todo_id
    except Exception as exc:
        raise ValueError("Malformed pagination cursor.") from exc

    # Unknown discriminator: reject rather than silently coercing to str.
    raise ValueError(f"Unknown cursor value type: {kind!r}.")
