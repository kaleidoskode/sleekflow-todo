"""Request and response models for the TODO API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import Priority, RecurrenceUnit, Status

PRIORITY_TO_NAME = {Priority.LOW: "low", Priority.MEDIUM: "medium", Priority.HIGH: "high"}
NAME_TO_PRIORITY = {v: k for k, v in PRIORITY_TO_NAME.items()}

_NAME_FIELD = Field(
    description="Short label for the todo item. 1-200 characters.",
    examples=["Review billing webhook"],
)
_DESC_FIELD = Field(
    default=None,
    description="Optional longer description or notes. Up to 4000 characters.",
    examples=["The webhook handler needs retry logic for 409s."],
)
_DUE_DATE_FIELD = Field(
    default=None,
    description=(
        "ISO 8601 timestamp in UTC (``YYYY-MM-DDTHH:MM:SSZ``). "
        "Required when setting a recurrence rule."
    ),
    examples=["2026-12-31T09:00:00Z"],
)
_PRIORITY_FIELD = Field(
    default="medium",
    description="How urgent this todo is.",
    examples=["low", "medium", "high"],
)
_RECURRENCE_UNIT_FIELD = Field(
    default=None,
    description="Repeats every `recurrence_interval` of this unit. Set together with interval.",
    examples=["day", "week", "month"],
)
_RECURRENCE_INTERVAL_FIELD = Field(
    default=None,
    ge=1,
    le=365,
    description="Number of units between occurrences. Set together with recurrence_unit.",
    examples=[1, 2, 3],
)


class TodoBase(BaseModel):
    """Fields shared by create and update operations."""

    name: str = _NAME_FIELD
    description: str | None = _DESC_FIELD
    due_date: datetime | None = _DUE_DATE_FIELD
    priority: str = _PRIORITY_FIELD
    recurrence_unit: RecurrenceUnit | None = _RECURRENCE_UNIT_FIELD
    recurrence_interval: int | None = _RECURRENCE_INTERVAL_FIELD

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        value = value.strip()
        if value == "":
            raise ValueError("Give the todo a name.")
        if len(value) > 200:
            raise ValueError("Name must be 200 characters or fewer.")
        return value

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 4000:
            raise ValueError("Notes must be 4000 characters or fewer.")
        return value

    @model_validator(mode="after")
    def check_recurrence_pair(self) -> "TodoBase":
        if (self.recurrence_unit is None) != (self.recurrence_interval is None):
            raise ValueError("Choose how often it repeats and how many units between each one.")
        if self.recurrence_unit is not None and self.due_date is None:
            raise ValueError("Pick a due date first: a repeating todo counts from it.")
        if self.priority not in NAME_TO_PRIORITY:
            raise ValueError("Priority must be low, medium or high.")
        return self


class TodoCreate(TodoBase):
    """Payload for ``POST /api/todos`` — every field from TodoBase applies."""


class TodoUpdate(BaseModel):
    """Payload for ``PATCH /api/todos/{id}``.

    Every field is optional — only send what changed.  Status is deliberately
    excluded: use ``POST /api/todos/{id}/status`` to transition it.
    Unknown fields are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None)
    description: str | None = Field(default=None)
    due_date: datetime | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp.",
        examples=["2026-12-31T09:00:00Z"],
    )
    priority: str | None = None
    recurrence_unit: RecurrenceUnit | None = None
    recurrence_interval: int | None = Field(default=None, ge=1, le=365)

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str | None) -> str | None:
        if value is None:
            return value  # the model validator below rejects an explicit null
        value = value.strip()
        if value == "":
            raise ValueError("Give the todo a name.")
        if len(value) > 200:
            raise ValueError("Name must be 200 characters or fewer.")
        return value

    @field_validator("description")
    @classmethod
    def check_description(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 4000:
            raise ValueError("Notes must be 4000 characters or fewer.")
        return value

    @model_validator(mode="after")
    def check_explicit_nulls_and_priority(self) -> "TodoUpdate":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Give the todo a name.")
        if "priority" in self.model_fields_set and self.priority not in NAME_TO_PRIORITY:
            raise ValueError("Priority must be low, medium or high.")
        return self


class TodoRead(BaseModel):
    """A todo as returned by the API — the canonical response shape."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Unique identifier.")
    name: str = Field(description="Short label.", examples=["Review billing webhook"])
    description: str | None = Field(
        default=None, description="Optional longer description.", examples=["Needs retry logic."]
    )
    due_date: datetime | None = Field(
        default=None,
        description="ISO 8601 UTC timestamp. Null if no deadline is set.",
        examples=["2026-12-31T09:00:00Z"],
    )
    status: Status = Field(description="Current lifecycle stage.")
    priority: str = Field(description="How urgent this todo is.", examples=["low", "medium", "high"])
    recurrence_unit: RecurrenceUnit | None = Field(
        default=None, description="Recurrence unit, if this todo repeats."
    )
    recurrence_interval: int | None = Field(
        default=None, description="Recurrence interval, if this todo repeats."
    )
    recurrence_series_id: UUID | None = Field(
        default=None, description="Groups all occurrences of one series."
    )
    unmet_dependency_count: int = Field(
        description="Number of incomplete, not-deleted dependencies."
    )
    is_blocked: bool = Field(
        description="True when at least one dependency is still incomplete."
    )
    depends_on: list[UUID] = Field(
        default_factory=list,
        description="IDs this todo directly depends on. Only populated on single-todo GET; "
        "always empty in list responses to avoid N+1 queries.",
    )
    updated_by: str | None = Field(
        default=None,
        description="Username of whoever last changed this todo. Null for rows that predate "
        "accounts, or for seeded data.",
        examples=["ada"],
    )
    version: int = Field(
        description="Incremented on every write. Send back as ``If-Match`` to detect conflicts."
    )
    deleted_at: datetime | None = Field(
        default=None, description="When this todo was soft-deleted, or null."
    )
    created_at: datetime = Field(description="When this todo was created.")
    updated_at: datetime = Field(description="When this todo was last changed.")

    @classmethod
    def from_todo(
        cls, todo, depends_on: list[UUID] | None = None, updated_by: str | None = None
    ) -> "TodoRead":
        # `updated_by` is passed in rather than read off a relationship: the
        # list endpoint resolves every actor in one query, so rendering a page
        # of 50 does not fire 50 lookups.
        return cls(
            updated_by=updated_by,
            id=todo.id,
            name=todo.name,
            description=todo.description,
            due_date=todo.due_date,
            status=todo.status,
            priority=PRIORITY_TO_NAME[Priority(todo.priority)],
            recurrence_unit=todo.recurrence_unit,
            recurrence_interval=todo.recurrence_interval,
            recurrence_series_id=todo.recurrence_series_id,
            unmet_dependency_count=todo.unmet_dependency_count,
            is_blocked=todo.unmet_dependency_count > 0,
            depends_on=depends_on or [],
            version=todo.version,
            deleted_at=todo.deleted_at,
            created_at=todo.created_at,
            updated_at=todo.updated_at,
        )


class TodoPage(BaseModel):
    """A page of todos — keyset-paginated so page 100 costs the same as page 1."""

    items: list[TodoRead] = Field(description="The todos on this page.")
    next_cursor: str | None = Field(
        description="Opaque cursor for the next page. Null means this is the last page.",
        examples=["eyJ0IjoiZHQiLCJ2IjoiMjAyNi0wNi0wMVQwOTowMDowMCswMDowMCIs..."],
    )


class StatusChange(BaseModel):
    """Payload for ``POST /api/todos/{id}/status``."""

    status: Status = Field(
        description="The target status. Must differ from the current status.",
        examples=["in_progress", "completed"],
    )


class StatusChangeResult(BaseModel):
    """Returned after a status transition. Carries the new occurrence if one was spawned."""

    todo: TodoRead = Field(description="The todo after the transition.")
    next_occurrence: TodoRead | None = Field(
        default=None,
        description="The next recurrence occurrence, if this was a recurring todo "
        "being completed. Null for non-recurring todos.",
    )
