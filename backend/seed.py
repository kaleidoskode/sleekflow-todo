"""Populate the database with a realistic dataset for performance verification.

Usage: python -m seed --count 10000
"""

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from uuid6 import uuid7

from app.db import SessionFactory
from app.domain.enums import Priority, RecurrenceUnit, Status
from app.models.dependency import TodoDependency
from app.models.todo import Todo

VERBS = ["Review", "Draft", "Ship", "Migrate", "Refactor", "Investigate", "Document", "Deploy"]
NOUNS = ["billing", "onboarding", "webhooks", "search index", "audit log", "rate limiter"]


async def seed(count: int, dependency_ratio: float = 0.3) -> None:
    # Reproducible structure: the RNG is seeded, so counts, names, statuses,
    # priorities and the edge set are identical across runs. Absolute timestamps
    # are not — due dates are anchored to wall-clock `now` so a seeded dataset
    # always looks current rather than months overdue.
    rng = random.Random(42)
    now = datetime.now(UTC)

    async with SessionFactory() as session:
        todos: list[Todo] = []
        for i in range(count):
            recurring = rng.random() < 0.1
            due = now + timedelta(days=rng.randint(-30, 120)) if rng.random() < 0.85 else None
            todo = Todo(
                id=uuid7(),
                name=f"{rng.choice(VERBS)} {rng.choice(NOUNS)} #{i}",
                description=f"Seeded item {i}." if rng.random() < 0.6 else None,
                due_date=due,
                status=rng.choice(list(Status)),
                priority=rng.choice(list(Priority)).value,
            )
            if recurring and due is not None:
                todo.recurrence_unit = rng.choice(list(RecurrenceUnit))
                todo.recurrence_interval = rng.choice([1, 1, 1, 2, 3])
                todo.recurrence_series_id = uuid7()
                todo.recurrence_anchor_due = due
            todos.append(todo)

        session.add_all(todos)
        await session.flush()

        # Edges point strictly backwards in creation order, so the graph is acyclic
        # by construction.
        edges = set()
        for index in range(1, len(todos)):
            if rng.random() < dependency_ratio:
                for _ in range(rng.randint(1, 3)):
                    target = rng.randint(0, index - 1)
                    edges.add((todos[index].id, todos[target].id))

        session.add_all(
            TodoDependency(todo_id=a, depends_on_id=b) for a, b in edges
        )
        await session.commit()

        await session.execute(
            text("""
                UPDATE todos t SET unmet_dependency_count = COALESCE((
                    SELECT count(*) FROM todo_dependencies d
                    JOIN todos dep ON dep.id = d.depends_on_id
                    WHERE d.todo_id = t.id
                      AND dep.status <> 'completed' AND dep.deleted_at IS NULL
                ), 0)
            """)
        )
        await session.execute(text("ANALYZE todos"))
        await session.commit()

    print(f"Seeded {count} todos and {len(edges)} dependency edges.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10_000)
    asyncio.run(seed(parser.parse_args().count))
