from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Select, bindparam, cast, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.core.pagination import SortField, SortSpec, decode_cursor, encode_cursor
from app.domain.enums import Status

# Imported rather than redeclared: the indexes in the model are built on these
# exact values, and a second copy that drifted would cost a sequential scan
# with nothing failing to announce it.
from app.models.todo import DATE_MAX, DATE_MIN, Todo


@dataclass
class TodoFilter:
    statuses: list[Status] = field(default_factory=list)
    priorities: list[int] = field(default_factory=list)
    due_before: datetime | None = None
    due_after: datetime | None = None
    blocked: bool | None = None
    include_deleted: bool = False


def _sentinel(value: datetime):
    """The COALESCE sentinel, rendered into the SQL rather than bound.

    This is not a style choice. `ix_todos_live_due_asc` / `_desc` are indexes on
    `coalesce(due_date, <constant>)`, and PostgreSQL matches an expression index
    by comparing expression trees. Sent as `$1` the constant is unknown when a
    generic plan is built, the index cannot be matched, and the query falls back
    to sorting the whole table.

    Worse, it fails *intermittently*: whether a prepared statement uses a custom
    plan (parameter known, index matched) or a generic one is a planner
    heuristic. Measured on 10,007 rows, the ascending sort happened to keep its
    custom plan at 0.9 ms while the descending sort went generic at 4.8 ms —
    the same code, the same index, five times slower in one direction. Both are
    0.9 ms with the sentinel inlined.

    Safe to inline: these are module constants, never user input. The cursor
    anchor beside them stays a bound parameter, because that *is* user input.
    """
    return bindparam(None, value, DateTime(timezone=True), literal_execute=True)


def sort_expression(sort: SortSpec):
    """Always non-null, so row-value comparison in the keyset predicate is valid."""
    if sort.field is SortField.DUE_DATE:
        return func.coalesce(
            Todo.due_date, _sentinel(DATE_MIN if sort.descending else DATE_MAX)
        )
    if sort.field is SortField.PRIORITY:
        return Todo.priority
    if sort.field is SortField.STATUS:
        return Todo.status
    return Todo.name


class TodoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, todo_id: UUID, *, include_deleted: bool = False) -> Todo | None:
        stmt = select(Todo).where(Todo.id == todo_id)
        if not include_deleted:
            stmt = stmt.where(Todo.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert(self, todo: Todo) -> Todo:
        self.session.add(todo)
        await self.session.flush()
        await self.session.refresh(todo)
        return todo

    async def update_versioned(
        self,
        todo_id: UUID,
        expected_version: int,
        values: dict[str, Any],
        *,
        require_unblocked: bool = False,
    ) -> Todo | None:
        """Single-statement compare-and-set. None means lost race, row gone, or (if
        `require_unblocked`) a concurrent re-block — the caller distinguishes those by
        re-reading.
        """
        conditions = [
            Todo.id == todo_id,
            Todo.version == expected_version,
            Todo.deleted_at.is_(None),
        ]
        if require_unblocked:
            conditions.append(Todo.unmet_dependency_count == 0)
        stmt = (
            update(Todo)
            .where(*conditions)
            .values(**values, version=Todo.version + 1, updated_at=datetime.now(UTC))
            .returning(Todo)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def soft_delete(
        self, todo_id: UUID, expected_version: int, actor_id: UUID | None = None
    ) -> Todo | None:
        return await self.update_versioned(
            todo_id,
            expected_version,
            {"deleted_at": datetime.now(UTC), "updated_by_id": actor_id},
        )

    async def restore(
        self, todo_id: UUID, expected_version: int, actor_id: UUID | None = None
    ) -> Todo | None:
        stmt = (
            update(Todo)
            .where(
                Todo.id == todo_id,
                Todo.version == expected_version,
                Todo.deleted_at.is_not(None),
            )
            .values(
                deleted_at=None,
                updated_by_id=actor_id,
                version=Todo.version + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(Todo)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def _apply_filters(self, stmt: Select, f: TodoFilter) -> Select:
        if not f.include_deleted:
            stmt = stmt.where(Todo.deleted_at.is_(None))
        if f.statuses:
            stmt = stmt.where(Todo.status.in_(f.statuses))
        if f.priorities:
            stmt = stmt.where(Todo.priority.in_(f.priorities))
        if f.due_before is not None:
            stmt = stmt.where(Todo.due_date < f.due_before)
        if f.due_after is not None:
            stmt = stmt.where(Todo.due_date > f.due_after)
        if f.blocked is True:
            stmt = stmt.where(Todo.unmet_dependency_count > 0)
        elif f.blocked is False:
            stmt = stmt.where(Todo.unmet_dependency_count == 0)
        return stmt

    async def list_page(
        self, filters: TodoFilter, sort: SortSpec, cursor: str | None, limit: int
    ) -> tuple[list[Todo], str | None]:
        key = sort_expression(sort)
        stmt = self._apply_filters(select(Todo), filters)

        if cursor is not None:
            last_value, last_id = decode_cursor(cursor)
            if sort.field is SortField.STATUS:
                key_for_row = cast(key, Todo.status.type)
                anchor_value = cast(last_value, Todo.status.type)
            else:
                key_for_row = key
                anchor_value = last_value
            row = tuple_(key_for_row, Todo.id)
            anchor = tuple_(anchor_value, last_id)
            stmt = stmt.where(row < anchor if sort.descending else row > anchor)

        order = (key.desc(), Todo.id.desc()) if sort.descending else (key.asc(), Todo.id.asc())
        # Fetch one extra row to learn whether another page exists, without a COUNT.
        stmt = stmt.order_by(*order).limit(limit + 1)

        rows = list((await self.session.execute(stmt)).scalars().all())
        has_more = len(rows) > limit
        rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            raw_value = getattr(last, sort.field.value)
            if sort.field is SortField.DUE_DATE and raw_value is None:
                raw_value = DATE_MIN if sort.descending else DATE_MAX
            if sort.field is SortField.STATUS:
                raw_value = str(raw_value)
            next_cursor = encode_cursor(raw_value, last.id)

        return rows, next_cursor
