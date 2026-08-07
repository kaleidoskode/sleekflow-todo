from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.domain.enums import Priority, RecurrenceUnit, Status
from app.models.base import Base


class Todo(Base):
    __tablename__ = "todos"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(4000))
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Declaration order is load-bearing: PostgreSQL sorts native enums by it,
    # which is exactly the lifecycle order "sort by status" needs.
    status: Mapped[Status] = mapped_column(
        Enum(Status, name="todo_status", native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=Status.NOT_STARTED,
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=Priority.MEDIUM)

    recurrence_unit: Mapped[RecurrenceUnit | None] = mapped_column(
        Enum(RecurrenceUnit, name="recurrence_unit", native_enum=True,
             values_callable=lambda e: [m.value for m in e])
    )
    recurrence_interval: Mapped[int | None] = mapped_column(Integer)
    recurrence_series_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    recurrence_anchor_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrence_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Derived state, maintained transactionally. Never bump `version` when writing it.
    unmet_dependency_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Attribution. Nullable because rows predating auth (and seeded rows) have
    # no actor, and ON DELETE SET NULL so removing an account never destroys a
    # todo. Written only by real user actions — never by a derived-state
    # recompute, which must stay invisible to other clients.
    created_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "(recurrence_unit IS NULL AND recurrence_interval IS NULL)"
            " OR (recurrence_unit IS NOT NULL AND recurrence_interval >= 1)",
            name="ck_todos_recurrence_complete",
        ),
        CheckConstraint("priority IN (10, 20, 30)", name="ck_todos_priority"),
        # Partial indexes: every default listing filters deleted rows out, so the
        # index should not carry them.
        Index("ix_todos_live_due", "due_date", "id", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_todos_live_priority", "priority", "id", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_todos_live_status", "status", "id", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_todos_live_name", "name", "id", postgresql_where=text("deleted_at IS NULL")),
        Index("ix_todos_live_blocked", "unmet_dependency_count",
              postgresql_where=text("deleted_at IS NULL")),
        Index("ix_todos_series", "recurrence_series_id"),
    )
