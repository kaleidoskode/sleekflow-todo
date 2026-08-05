from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index
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

    __table_args__ = (
        CheckConstraint("todo_id <> depends_on_id", name="ck_no_self_dependency"),
        # Reverse lookup: "who depends on X" drives count recomputation.
        Index("ix_dependencies_depends_on", "depends_on_id"),
    )
