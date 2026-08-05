from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import Priority, RecurrenceUnit, Status

PRIORITY_TO_NAME = {Priority.LOW: "low", Priority.MEDIUM: "medium", Priority.HIGH: "high"}
NAME_TO_PRIORITY = {v: k for k, v in PRIORITY_TO_NAME.items()}


class TodoBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    due_date: datetime | None = None
    priority: str = "medium"
    recurrence_unit: RecurrenceUnit | None = None
    recurrence_interval: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def check_recurrence_pair(self) -> "TodoBase":
        if (self.recurrence_unit is None) != (self.recurrence_interval is None):
            raise ValueError("recurrence_unit and recurrence_interval must be set together")
        if self.recurrence_unit is not None and self.due_date is None:
            raise ValueError("a recurring todo requires a due_date to anchor its schedule")
        if self.priority not in NAME_TO_PRIORITY:
            raise ValueError("priority must be one of: low, medium, high")
        return self


class TodoCreate(TodoBase):
    pass


class TodoUpdate(BaseModel):
    """All fields optional — this is a PATCH. Status is not settable here."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    due_date: datetime | None = None
    priority: str | None = None
    recurrence_unit: RecurrenceUnit | None = None
    recurrence_interval: int | None = Field(default=None, ge=1, le=365)


class TodoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    due_date: datetime | None
    status: Status
    priority: str
    recurrence_unit: RecurrenceUnit | None
    recurrence_interval: int | None
    recurrence_series_id: UUID | None
    unmet_dependency_count: int
    is_blocked: bool
    depends_on: list[UUID] = []
    version: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_todo(cls, todo, depends_on: list[UUID] | None = None) -> "TodoRead":
        return cls(
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
    items: list[TodoRead]
    next_cursor: str | None
