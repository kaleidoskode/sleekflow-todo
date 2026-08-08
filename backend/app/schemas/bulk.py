"""Request and response shapes for batch operations.

Every item carries its own ``version``, because a batch cannot express
optimistic concurrency through ``If-Match``: there is one header and many
rows. Moving the precondition into the body keeps the guarantee per row —
a stale item fails on its own and the rest still apply.
"""

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import Status

# Bounded so one request cannot pin a worker indefinitely. Matches MAX_PAGE_SIZE:
# the largest page the API will hand out is the largest selection it will act on,
# so "select everything on screen" always fits in a single request.
MAX_BULK_ITEMS = 200


class BulkItem(BaseModel):
    """One row and the version the caller believes it is at."""

    id: UUID
    version: int = Field(ge=1)


class _BulkRequest(BaseModel):
    items: list[BulkItem]

    @field_validator("items")
    @classmethod
    def check_items(cls, value: list[BulkItem]) -> list[BulkItem]:
        if len(value) == 0:
            raise ValueError("Select at least one todo.")
        if len(value) > MAX_BULK_ITEMS:
            raise ValueError(f"Too many todos at once. {MAX_BULK_ITEMS} is the maximum.")
        ids = [item.id for item in value]
        if len(set(ids)) != len(ids):
            # Two entries for one row cannot both be right: the second carries a
            # version the first just superseded. Rejecting is clearer than
            # silently failing half the pair on a conflict it caused itself.
            raise ValueError("The same todo appears more than once.")
        return value


class BulkStatusChange(_BulkRequest):
    status: Status


class BulkDelete(_BulkRequest):
    pass


class BulkItemResult(BaseModel):
    """The outcome for one row. `ok` is the only field always meaningful."""

    id: UUID
    ok: bool
    version: int | None = Field(default=None)
    code: str | None = Field(default=None)
    detail: str | None = Field(default=None)


class BulkResult(BaseModel):
    """Per-item outcomes, so one blocked todo does not fail the batch.

    Always returned with ``200``: the batch request itself succeeded, and the
    individual outcomes are the payload. ``207 Multi-Status`` was considered
    and rejected — it is a WebDAV extension whose body format is XML, and
    reusing the code with a different body invites clients to guess.
    """

    succeeded: int
    failed: int
    results: list[BulkItemResult]
