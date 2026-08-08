"""Measure what per-item isolation costs in the batch endpoints.

Batch items each run in their own transaction so one refusal does not sink the
rest, which trades a round trip per item for a per-item answer. This puts a
number on that trade. Results are in docs/performance.md.

Runs against TEST_DATABASE_URL and drops its schema first, so it refuses to
start unless that points at todo_test — the real board is never touched.

    cd backend && uv run python bench_bulk.py
"""

import asyncio
import statistics
import time

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import get_session, get_session_factory
from app.main import create_app
from app.models.base import Base

SIZES = (10, 50, 200)
RUNS = 5

engine = create_async_engine(settings.test_database_url)
Factory = async_sessionmaker(engine, expire_on_commit=False)


async def main() -> None:
    if "todo_test" not in settings.test_database_url:
        raise SystemExit(
            "Refusing to run: this drops the schema, and TEST_DATABASE_URL does "
            f"not point at todo_test (got {settings.test_database_url!r})."
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()

    async def override_session():
        async with Factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_session_factory] = lambda: Factory

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bench", timeout=120) as c:
        registered = await c.post(
            "/api/auth/register", json={"username": "bench", "password": "bench-password"}
        )
        c.headers["Authorization"] = f"Bearer {registered.json()['access_token']}"

        print(f"{'items':>6}  {'median':>10}  {'per item':>9}  {'min':>7}  {'max':>7}")
        for size in SIZES:
            todos = []
            for i in range(size):
                created = await c.post("/api/todos", json={"name": f"bulk bench {size}-{i}"})
                todos.append(created.json())

            items = [{"id": t["id"], "version": t["version"]} for t in todos]
            samples = []
            # Alternating targets keep every run a real transition rather than a
            # no-op, and each run feeds the returned versions into the next.
            targets = ["in_progress", "not_started"] * RUNS
            for target in targets[:RUNS]:
                start = time.perf_counter()
                response = await c.post(
                    "/api/todos/bulk/status", json={"items": items, "status": target}
                )
                samples.append((time.perf_counter() - start) * 1000)
                body = response.json()
                assert body["succeeded"] == size, body
                items = [{"id": r["id"], "version": r["version"]} for r in body["results"]]

            median = statistics.median(samples)
            print(
                f"{size:6d}  {median:8.1f} ms  {median / size:6.2f} ms  "
                f"{min(samples):5.0f} ms  {max(samples):5.0f} ms"
            )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
