from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TodoDependency(Base):
    __tablename__ = "todo_dependencies"

    todo_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("todos.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("todos.id", ondelete="CASCADE"), primary_key=True
    )

    # Attribution lives on the edge, not on the dependent todo. Adding a link
    # changes `unmet_dependency_count` and nothing else about the todo's own
    # columns — and that count is deliberately maintained without bumping
    # `version`, so claiming an author on the todo would leave two clients
    # disagreeing about who last touched it with no way to detect it.
    created_by_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("todo_id <> depends_on_id", name="ck_no_self_dependency"),
        # Reverse lookup: "who depends on X" drives count recomputation.
        Index("ix_dependencies_depends_on", "depends_on_id"),
    )
