"""Guards on the query plans the 10,000-item requirement depends on.

Timings make bad assertions — they vary with the machine, the cache and what
else is running. The *plan* does not: either the sort is served by an index or
the database is sorting the table. These tests assert the shape.

They exist because the default sort silently regressed exactly this way. The
sort key is `coalesce(due_date, sentinel)` because `due_date` is nullable, and
the index was on the raw column — which PostgreSQL cannot use for an expression.
Nothing failed, no test broke, and every page quietly planned as a sequential
scan plus a top-N sort of the whole table.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import SortSpec
from app.models.todo import Todo
from app.repositories.todo_repo import TodoFilter, TodoRepository, sort_expression

# Enough rows that the planner prefers an index over scanning a tiny table.
# Below a few thousand a Seq Scan is genuinely cheaper and the assertion would
# fail for the right reason at the wrong time.
ROW_COUNT = 3000

SORTS = ["due_date", "-due_date", "priority", "-priority", "status", "name"]


@pytest.fixture
async def _planning_rows(session: AsyncSession) -> None:
    """A table big enough for index plans to win.

    Function-scoped because `session` truncates between tests — one
    `INSERT ... SELECT` over generate_series, so the repetition is cheap.
    """
    await session.execute(
        text(
            """
            INSERT INTO todos (id, name, status, priority, due_date,
                               unmet_dependency_count, version, created_at, updated_at)
            SELECT
                gen_random_uuid(),
                'plan row ' || g,
                'not_started',
                20,
                -- One row in five has no due date, so the NULL path is real.
                -- mod() rather than `%`: the driver reads a bare percent sign
                -- as parameter syntax.
                CASE WHEN mod(g, 5) = 0 THEN NULL
                     ELSE now() + (g || ' minutes')::interval END,
                0, 1, now(), now()
            FROM generate_series(1, :n) AS g
            """
        ),
        {"n": ROW_COUNT},
    )
    await session.execute(text("ANALYZE todos"))
    await session.commit()


async def _plan(session: AsyncSession, sort_raw: str) -> str:
    """EXPLAIN the statement the repository actually builds for this sort."""
    sort = SortSpec.parse(sort_raw)
    key = sort_expression(sort)
    order = (key.desc(), Todo.id.desc()) if sort.descending else (key.asc(), Todo.id.asc())
    stmt = (
        select(Todo).where(Todo.deleted_at.is_(None)).order_by(*order).limit(51)
    )
    sql = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    rows = (await session.execute(text("EXPLAIN " + sql))).scalars().all()
    return "\n".join(rows)


class TestSentinelIsInlined:
    """Guards the fix that `TestListingUsesIndexes` structurally cannot catch.

    Those tests EXPLAIN a statement compiled with `literal_binds`, which inlines
    every value — so they would pass even if the running query sent the sentinel
    as `$1`. And sending it as a parameter is exactly the bug: an expression
    index is built on a constant, so a generic plan cannot match it and the
    query silently sorts the table instead.

    It failed intermittently rather than outright, which is what makes it worth
    a dedicated test: whether a prepared statement gets a custom plan (parameter
    known, index matched) or a generic one is a planner heuristic. Measured, the
    ascending sort kept its custom plan at 0.9 ms while the descending sort went
    generic at 4.8 ms — same code, same index.
    """

    @pytest.mark.parametrize("sort_raw", ["due_date", "-due_date"])
    def test_the_coalesce_sentinel_is_not_a_bound_parameter(self, sort_raw: str) -> None:
        expression = sort_expression(SortSpec.parse(sort_raw))
        sql = str(
            expression.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"render_postcompile": True},
            )
        )
        expected = "0001-01-01" if sort_raw.startswith("-") else "9999-12-31"
        assert expected in sql, f"sentinel is not inlined:\n{sql}"
        assert "POSTCOMPILE" not in sql

    def test_the_cursor_anchor_is_still_bound(self) -> None:
        """The sentinel is inlined because it is a constant we control.

        The cursor value beside it comes from the client and must stay a bound
        parameter — this test exists so a future 'make it all literal' does not
        quietly turn user input into SQL text.
        """
        statement = (
            select(Todo)
            .where(Todo.deleted_at.is_(None))
            .where(sort_expression(SortSpec.parse("due_date")) > datetime.now(UTC))
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        # One bind remains: the comparison value. The sentinel is not among them.
        assert any(isinstance(v, datetime) for v in compiled.params.values())


@pytest.mark.usefixtures("_planning_rows")
class TestListingUsesIndexes:
    @pytest.mark.parametrize("sort_raw", SORTS)
    async def test_sort_is_served_by_an_index(
        self, session: AsyncSession, sort_raw: str
    ) -> None:
        plan = await _plan(session, sort_raw)
        assert "Index Scan" in plan, f"sort={sort_raw} is not index-served:\n{plan}"

    @pytest.mark.parametrize("sort_raw", SORTS)
    async def test_the_database_is_not_sorting_the_table(
        self, session: AsyncSession, sort_raw: str
    ) -> None:
        """A `Sort` node means every page pays for the whole table.

        This is the assertion that would have caught the regression: the plan
        was still O(1) in *page depth*, so the keyset claim held, but it was
        O(n) in table size — which is the claim that actually matters at 10k.
        """
        plan = await _plan(session, sort_raw)
        assert "Sort  " not in plan, f"sort={sort_raw} sorts the table:\n{plan}"
        assert "Seq Scan" not in plan, f"sort={sort_raw} scans the table:\n{plan}"

    async def test_undated_todos_survive_paging(self, session: AsyncSession) -> None:
        """The reason the sort key is COALESCE'd in the first place.

        Comparing a row value against NULL yields NULL, not true — so without
        the sentinel every undated todo vanishes from the page after a cursor.
        """
        repo = TodoRepository(session)
        seen: list[str] = []
        cursor = None
        for _ in range(200):  # bounded so a paging bug cannot hang the suite
            rows, cursor = await repo.list_page(
                TodoFilter(), SortSpec.parse("due_date"), cursor, 200
            )
            seen.extend(str(r.id) for r in rows)
            if cursor is None:
                break

        assert cursor is None, "paging did not terminate"
        assert len(seen) == len(set(seen)), "a todo appeared on two pages"

        total = (
            await session.execute(
                text("SELECT count(*) FROM todos WHERE deleted_at IS NULL")
            )
        ).scalar()
        undated = (
            await session.execute(
                text(
                    "SELECT count(*) FROM todos "
                    "WHERE deleted_at IS NULL AND due_date IS NULL"
                )
            )
        ).scalar()
        assert undated > 0, "fixture did not create undated rows; test proves nothing"
        assert len(seen) == total, f"paging returned {len(seen)} of {total} rows"
