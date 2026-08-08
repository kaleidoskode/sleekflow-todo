"""index the coalesced due-date sort key

`due_date` is nullable, so the keyset sort key is `coalesce(due_date, sentinel)`
— a row-value comparison against NULL yields NULL and would silently drop every
undated todo from the page after a cursor.

The index was on the raw `due_date` column, which PostgreSQL cannot use for
`ORDER BY coalesce(due_date, ...)`: expression indexes are matched by comparing
expression trees, and a bare column is not that expression. Measured on 10,007
live rows, the default sort planned as a Seq Scan plus a top-N heapsort of the
whole table at 5.32 ms; with these indexes it is an Index Scan at 0.09 ms.

Two indexes because ascending and descending coalesce to different sentinels
(both put undated todos last). DESC is served by scanning its index backwards.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-08

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: str | Sequence[str] | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Must match app.models.todo.DATE_MAX / DATE_MIN exactly. A sentinel that
# differs at all produces an index the planner will not match, and nothing
# fails — the query just quietly goes back to scanning the table.
_ASC = "coalesce(due_date, '9999-12-31 00:00:00+00'::timestamptz)"
_DESC = "coalesce(due_date, '0001-01-01 00:00:00+00'::timestamptz)"
_LIVE = "deleted_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        f"CREATE INDEX ix_todos_live_due_asc ON todos (({_ASC}), id) WHERE {_LIVE}"
    )
    op.execute(
        f"CREATE INDEX ix_todos_live_due_desc ON todos (({_DESC}), id) WHERE {_LIVE}"
    )
    # `ix_todos_live_due` on the raw column is deliberately KEPT. It looked like
    # dead weight once the sort moved to the expression indexes, but the
    # `due_before` / `due_after` filters are predicates on the bare column, and
    # an expression index cannot serve those. Which index the planner picks
    # depends on the sort and the selectivity of the window, so dropping it was
    # an optimisation that could not be justified by measurement — and an
    # unused index costs far less than a filter that starts scanning.


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_todos_live_due_desc', table_name='todos')
    op.drop_index('ix_todos_live_due_asc', table_name='todos')
