# SleekFlow TODO Application — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared TODO list application — FastAPI REST API plus a React web UI — covering CRUD, recurring tasks, task dependencies, and filtering/sorting, satisfying concurrent multi-user access, soft delete, and 10,000+ item performance.

**Architecture:** Layered FastAPI backend (routers → services → repositories → PostgreSQL) with domain rules isolated as pure functions for testability. Optimistic concurrency via a `version` column and `If-Match`. Soft delete via `deleted_at` with a repository-level filter. Blocked/unblocked filtering backed by a transactionally-maintained `unmet_dependency_count` column. React + Vite SPA consuming the API through TanStack Query.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Alembic, PostgreSQL 16, pytest + pytest-asyncio + httpx; React 18 + Vite + TypeScript + TanStack Query; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-05-sleekflow-todo-design.md`

## Global Constraints

- Python 3.12; PostgreSQL 16; Node 20.
- All timestamps are `timestamptz` and stored in UTC. Never use naive datetimes.
- Every mutating endpoint requires an `If-Match` header carrying the row `version`. Absent → `428`. Mismatched → `409`.
- All error responses use RFC 9457 Problem Details with content type `application/problem+json`.
- Domain logic in `backend/app/domain/` must be **pure** — no imports from `sqlalchemy`, `fastapi`, or any module under `app.repositories` / `app.services` / `app.routers`.
- `unmet_dependency_count` is derived state. Recomputing it must **never** increment `version` (doing so would fire spurious `409`s at clients holding a valid version).
- Status enum values, in this exact declaration order: `not_started`, `in_progress`, `completed`, `archived`. The order is load-bearing — PostgreSQL sorts native enums by declaration order, which is what "sort by status" uses.
- Priority is stored as `smallint`: `low=10`, `medium=20`, `high=30`. Exposed over the API as the string form only.
- Commit after every task. Conventional Commits style (`feat:`, `test:`, `fix:`, `docs:`, `chore:`).

---

## File Structure

```
docker-compose.yml               postgres + api + web
.env.example

backend/
  pyproject.toml
  alembic.ini
  Dockerfile
  alembic/
    env.py
    versions/
  app/
    main.py                      app factory, router mounting, handler registration
    config.py                    pydantic-settings Settings
    db.py                        async engine, session factory, get_session dependency
    errors.py                    domain exception types + Problem Details handlers
    pagination.py                keyset cursor encode/decode  (pure)
    domain/
      enums.py                   Status, Priority, RecurrenceUnit          (pure)
      recurrence.py              next_occurrence()                          (pure)
      transitions.py             validate_transition()                      (pure)
    models/
      base.py                    DeclarativeBase
      todo.py                    Todo ORM model
      dependency.py              TodoDependency ORM model
    schemas/
      todo.py                    Pydantic request/response models
      errors.py                  ProblemDetail response model
    repositories/
      todo_repo.py               queries, filtering, sorting, keyset paging, soft delete
      dependency_repo.py         edges, cycle detection, count recomputation
    services/
      todo_service.py            CRUD orchestration + concurrency
      dependency_service.py      edge add/remove + cycle guard + count refresh
      status_service.py          transition validation + recurrence spawn
    routers/
      health.py
      todos.py
      dependencies.py
  seed.py                        10k dataset generator
  tests/
    conftest.py                  DB fixtures, client fixture
    unit/
      test_recurrence.py
      test_transitions.py
      test_pagination.py
    integration/
      test_crud.py
      test_concurrency.py
      test_listing.py
      test_dependencies.py
      test_status.py

frontend/
  package.json
  vite.config.ts
  Dockerfile
  src/
    main.tsx
    App.tsx
    api/
      types.ts                   generated-by-hand TS mirrors of the API schemas
      client.ts                  fetch wrapper: If-Match, Problem Details parsing
      todos.ts                   TanStack Query hooks
    components/
      TodoList.tsx
      FilterBar.tsx
      TodoForm.tsx
      DependencyPicker.tsx
      StatusControl.tsx
      ConflictBanner.tsx
```

**Boundary rationale:** `domain/` holds the three behaviours with real edge cases (recurrence date math, transition guards, cursor encoding) as pure functions, so they are tested without a database or HTTP layer. `repositories/` is the only place SQLAlchemy appears. `services/` composes the two and owns transactions.

---

# Phase 1 — Foundation

### Task 1: Project scaffold, Docker Compose, health endpoint

**Files:**
- Create: `docker-compose.yml`, `.env.example`, `backend/pyproject.toml`, `backend/Dockerfile`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/db.py`, `backend/app/main.py`, `backend/app/routers/__init__.py`, `backend/app/routers/health.py`
- Test: `backend/tests/conftest.py`, `backend/tests/integration/test_health.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` (attrs `database_url: str`, `test_database_url: str`); `get_session() -> AsyncIterator[AsyncSession]`; `create_app() -> FastAPI`.

- [ ] **Step 1: Create `backend/pyproject.toml`**

```toml
[project]
name = "sleekflow-todo"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "uuid6>=2024.7.10",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.7",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Create `backend/app/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://todo:todo@localhost:5432/todo"
    test_database_url: str = "postgresql+asyncpg://todo:todo@localhost:5432/todo_test"


settings = Settings()
```

- [ ] **Step 3: Create `backend/app/db.py`**

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session
```

- [ ] **Step 4: Create `backend/app/routers/health.py`**

```python
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 5: Create `backend/app/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health


def create_app() -> FastAPI:
    app = FastAPI(
        title="SleekFlow TODO API",
        version="0.1.0",
        description="Shared TODO list with dependencies, recurrence, and optimistic concurrency.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )
    app.include_router(health.router)
    return app


app = create_app()
```

- [ ] **Step 6: Create `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: todo
      POSTGRES_PASSWORD: todo
      POSTGRES_DB: todo
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U todo"]
      interval: 3s
      retries: 10
    volumes:
      - pgdata:/var/lib/postgresql/data

  api:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://todo:todo@db:5432/todo
    ports: ["8000:8000"]
    depends_on:
      db: {condition: service_healthy}
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"

volumes:
  pgdata:
```

- [ ] **Step 7: Create `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 8: Write the failing test — `backend/tests/integration/test_health.py`**

```python
import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 9: Run the test**

Run: `cd backend && pytest tests/integration/test_health.py -v`
Expected: PASS.

- [ ] **Step 10: Verify the stack starts**

Run: `docker compose up -d db && curl -s localhost:8000/health` after `docker compose up -d`.
Expected: `{"status":"ok"}`.

- [ ] **Step 11: Commit**

```bash
git add docker-compose.yml backend/
git commit -m "feat: scaffold FastAPI backend with Docker Compose and health endpoint"
```

---

# Phase 2 — Pure domain logic

These tasks touch no database. They are where the edge cases live and where most test value is.

### Task 2: Domain enums and recurrence date math

**Files:**
- Create: `backend/app/domain/__init__.py`, `backend/app/domain/enums.py`, `backend/app/domain/recurrence.py`
- Test: `backend/tests/unit/test_recurrence.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Status` (`StrEnum`: `NOT_STARTED="not_started"`, `IN_PROGRESS="in_progress"`, `COMPLETED="completed"`, `ARCHIVED="archived"`)
  - `Priority` (`IntEnum`: `LOW=10`, `MEDIUM=20`, `HIGH=30`)
  - `RecurrenceUnit` (`StrEnum`: `DAY="day"`, `WEEK="week"`, `MONTH="month"`)
  - `add_interval(anchor: datetime, unit: RecurrenceUnit, steps: int) -> datetime`
  - `next_occurrence(anchor: datetime, unit: RecurrenceUnit, interval: int, current_index: int, now: datetime) -> tuple[datetime, int]` — returns `(next_due, next_index)`

**Design note — why an anchor and an index rather than "previous due + interval":** computing each occurrence from the previous one drifts on month ends. 31 Jan + 1 month clamps to 28 Feb; adding a month to *that* yields 28 Mar, and the series has silently lost its month-end anchoring. Computing occurrence *n* as `anchor + n × interval` with clamping applied fresh each time yields 31 Jan → 28 Feb → 31 Mar, which is correct. This requires two extra columns (`recurrence_anchor_due`, `occurrence_index`), added in Task 4.

- [ ] **Step 1: Create `backend/app/domain/enums.py`**

```python
from enum import IntEnum, StrEnum


class Status(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Priority(IntEnum):
    LOW = 10
    MEDIUM = 20
    HIGH = 30


class RecurrenceUnit(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
```

- [ ] **Step 2: Write the failing tests — `backend/tests/unit/test_recurrence.py`**

```python
from datetime import UTC, datetime

import pytest

from app.domain.enums import RecurrenceUnit
from app.domain.recurrence import add_interval, next_occurrence


def dt(y, m, d, h=9):
    return datetime(y, m, d, h, tzinfo=UTC)


def test_add_days():
    assert add_interval(dt(2026, 1, 1), RecurrenceUnit.DAY, 3) == dt(2026, 1, 4)


def test_add_weeks():
    assert add_interval(dt(2026, 1, 1), RecurrenceUnit.WEEK, 2) == dt(2026, 1, 15)


def test_add_months_simple():
    assert add_interval(dt(2026, 1, 15), RecurrenceUnit.MONTH, 1) == dt(2026, 2, 15)


def test_add_months_clamps_to_shorter_month():
    assert add_interval(dt(2026, 1, 31), RecurrenceUnit.MONTH, 1) == dt(2026, 2, 28)


def test_add_months_clamps_to_leap_february():
    assert add_interval(dt(2028, 1, 31), RecurrenceUnit.MONTH, 1) == dt(2028, 2, 29)


def test_add_months_crosses_year_boundary():
    assert add_interval(dt(2026, 11, 30), RecurrenceUnit.MONTH, 3) == dt(2027, 2, 28)


def test_monthly_series_does_not_drift_off_month_end():
    """The whole reason for anchor+index: 31 Jan must return to 31 Mar, not 28 Mar."""
    anchor = dt(2026, 1, 31)
    assert add_interval(anchor, RecurrenceUnit.MONTH, 1) == dt(2026, 2, 28)
    assert add_interval(anchor, RecurrenceUnit.MONTH, 2) == dt(2026, 3, 31)


def test_next_occurrence_advances_one_interval_when_future():
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 1, 10)
    due, index = next_occurrence(anchor, RecurrenceUnit.WEEK, 1, 0, now)
    assert due == dt(2026, 6, 8)
    assert index == 1


def test_next_occurrence_rolls_forward_past_missed_intervals():
    """Completing a weekly task 3 weeks late must not spawn a backdated occurrence."""
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 25, 12)
    due, index = next_occurrence(anchor, RecurrenceUnit.WEEK, 1, 0, now)
    assert due == dt(2026, 6, 29)
    assert index == 4


def test_next_occurrence_respects_custom_interval():
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 1, 10)
    due, index = next_occurrence(anchor, RecurrenceUnit.DAY, 3, 0, now)
    assert due == dt(2026, 6, 4)
    assert index == 1


def test_next_occurrence_continues_from_current_index():
    anchor = dt(2026, 6, 1)
    now = dt(2026, 6, 8, 10)
    due, index = next_occurrence(anchor, RecurrenceUnit.WEEK, 1, 1, now)
    assert due == dt(2026, 6, 15)
    assert index == 2


def test_next_occurrence_rejects_non_positive_interval():
    with pytest.raises(ValueError):
        next_occurrence(dt(2026, 6, 1), RecurrenceUnit.DAY, 0, 0, dt(2026, 6, 1))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_recurrence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.recurrence'`.

- [ ] **Step 4: Create `backend/app/domain/recurrence.py`**

```python
"""Pure recurrence date arithmetic. No database, no HTTP."""

import calendar
from datetime import datetime, timedelta

from app.domain.enums import RecurrenceUnit


def add_interval(anchor: datetime, unit: RecurrenceUnit, steps: int) -> datetime:
    """Advance `anchor` by `steps` units.

    Month arithmetic clamps to the last valid day of the target month, and is always
    computed from the anchor rather than from the previous result, so a series anchored
    on the 31st returns to the 31st in months that have one.
    """
    if unit is RecurrenceUnit.DAY:
        return anchor + timedelta(days=steps)
    if unit is RecurrenceUnit.WEEK:
        return anchor + timedelta(weeks=steps)
    if unit is RecurrenceUnit.MONTH:
        return _add_months(anchor, steps)
    raise ValueError(f"unsupported recurrence unit: {unit}")


def _add_months(anchor: datetime, months: int) -> datetime:
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return anchor.replace(year=year, month=month, day=min(anchor.day, last_day))


def next_occurrence(
    anchor: datetime,
    unit: RecurrenceUnit,
    interval: int,
    current_index: int,
    now: datetime,
) -> tuple[datetime, int]:
    """Return the (due date, occurrence index) of the occurrence following `current_index`.

    Advances past any intervals already in the past, so completing a long-overdue task
    yields a single future occurrence rather than a backlog of missed ones.
    """
    if interval < 1:
        raise ValueError("recurrence interval must be >= 1")

    index = current_index + 1
    due = add_interval(anchor, unit, interval * index)
    while due <= now:
        index += 1
        due = add_interval(anchor, unit, interval * index)
    return due, index
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_recurrence.py -v`
Expected: PASS — 12 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain backend/tests/unit/test_recurrence.py
git commit -m "feat: add pure recurrence date arithmetic with month-end clamping"
```

---

### Task 3: Status transition rules

**Files:**
- Create: `backend/app/domain/transitions.py`, `backend/app/errors.py`
- Test: `backend/tests/unit/test_transitions.py`

**Interfaces:**
- Consumes: `Status` from `app.domain.enums`.
- Produces:
  - `validate_transition(current: Status, target: Status, unmet_dependency_count: int) -> None` — raises on invalid.
  - Exception types in `app.errors`: `DomainError` (base, attrs `code: str`, `title: str`, `status_code: int`, `detail: str`, `extra: dict`), `BlockedByDependencies`, `InvalidTransition`, `DependencyCycle`, `VersionConflict`, `PreconditionRequired`, `NotFound`.

**Design note:** the state machine is deliberately permissive — every status is reachable from every other, including reopening a completed task and unarchiving. The only guard is the dependency rule, and it applies to `in_progress` **and** `completed` (spec §2.5: guarding only `in_progress` leaves a one-call bypass).

- [ ] **Step 1: Create `backend/app/errors.py`**

```python
from typing import Any


class DomainError(Exception):
    """Base for errors that map onto an RFC 9457 Problem Details response."""

    code: str = "DOMAIN_ERROR"
    title: str = "Domain error"
    status_code: int = 422

    def __init__(self, detail: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra or {}


class NotFound(DomainError):
    code = "NOT_FOUND"
    title = "Resource not found"
    status_code = 404


class VersionConflict(DomainError):
    code = "VERSION_CONFLICT"
    title = "Version conflict"
    status_code = 409


class PreconditionRequired(DomainError):
    code = "PRECONDITION_REQUIRED"
    title = "If-Match header required"
    status_code = 428


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"
    title = "Invalid status transition"
    status_code = 422


class BlockedByDependencies(DomainError):
    code = "BLOCKED_BY_DEPENDENCIES"
    title = "Blocked by incomplete dependencies"
    status_code = 422


class DependencyCycle(DomainError):
    code = "DEPENDENCY_CYCLE"
    title = "Dependency would create a cycle"
    status_code = 422
```

- [ ] **Step 2: Write the failing tests — `backend/tests/unit/test_transitions.py`**

```python
import pytest

from app.domain.enums import Status
from app.domain.transitions import validate_transition
from app.errors import BlockedByDependencies, InvalidTransition


def test_not_started_to_in_progress_allowed_when_unblocked():
    validate_transition(Status.NOT_STARTED, Status.IN_PROGRESS, 0)


def test_not_started_to_in_progress_blocked_by_dependencies():
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.NOT_STARTED, Status.IN_PROGRESS, 2)


def test_not_started_to_completed_blocked_by_dependencies():
    """The spec only guards in_progress; leaving completed open is a one-call bypass."""
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.NOT_STARTED, Status.COMPLETED, 1)


def test_in_progress_to_completed_blocked_by_dependencies():
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.IN_PROGRESS, Status.COMPLETED, 1)


def test_in_progress_to_completed_allowed_when_unblocked():
    validate_transition(Status.IN_PROGRESS, Status.COMPLETED, 0)


def test_archiving_is_never_blocked():
    validate_transition(Status.NOT_STARTED, Status.ARCHIVED, 5)


def test_reopening_a_completed_task_is_allowed():
    validate_transition(Status.COMPLETED, Status.NOT_STARTED, 0)


def test_reopening_to_in_progress_is_still_dependency_guarded():
    with pytest.raises(BlockedByDependencies):
        validate_transition(Status.COMPLETED, Status.IN_PROGRESS, 1)


def test_unarchiving_is_allowed():
    validate_transition(Status.ARCHIVED, Status.NOT_STARTED, 0)


def test_transition_to_same_status_is_rejected():
    with pytest.raises(InvalidTransition):
        validate_transition(Status.IN_PROGRESS, Status.IN_PROGRESS, 0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_transitions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.domain.transitions'`.

- [ ] **Step 4: Create `backend/app/domain/transitions.py`**

```python
"""Pure status-transition rules. No database, no HTTP."""

from app.domain.enums import Status
from app.errors import BlockedByDependencies, InvalidTransition

# Targets that require every dependency to be complete. `archived` is excluded
# deliberately: parking a blocked task is always legitimate.
DEPENDENCY_GUARDED_TARGETS = frozenset({Status.IN_PROGRESS, Status.COMPLETED})


def validate_transition(current: Status, target: Status, unmet_dependency_count: int) -> None:
    """Raise if moving from `current` to `target` is not permitted.

    The machine is permissive by design — reopening and unarchiving are both allowed.
    The dependency rule is the only real constraint.
    """
    if current is target:
        raise InvalidTransition(f"Todo is already in status '{target}'.")

    if target in DEPENDENCY_GUARDED_TARGETS and unmet_dependency_count > 0:
        raise BlockedByDependencies(
            f"Cannot move to '{target}' while {unmet_dependency_count} "
            f"dependenc{'y is' if unmet_dependency_count == 1 else 'ies are'} incomplete.",
            extra={"unmet_dependency_count": unmet_dependency_count},
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_transitions.py -v`
Expected: PASS — 10 tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/transitions.py backend/app/errors.py backend/tests/unit/test_transitions.py
git commit -m "feat: add status transition rules guarding in_progress and completed"
```

---

### Task 4: Keyset pagination cursors

**Files:**
- Create: `backend/app/pagination.py`
- Test: `backend/tests/unit/test_pagination.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SortField` (`StrEnum`: `DUE_DATE="due_date"`, `PRIORITY="priority"`, `STATUS="status"`, `NAME="name"`)
  - `SortSpec` dataclass (`field: SortField`, `descending: bool`), with `SortSpec.parse(raw: str) -> SortSpec` accepting `"due_date"` / `"-due_date"`.
  - `encode_cursor(sort_value: Any, todo_id: UUID) -> str`
  - `decode_cursor(cursor: str) -> tuple[Any, UUID]`

**Design note:** offset pagination degrades linearly and page 200 of a 10,000-item list is exactly what a reviewer probes. Keyset pagination compares the tuple `(sort_value, id)` against the cursor, so every page costs the same. The `id` tiebreaker is required — sort values are not unique.

- [ ] **Step 1: Write the failing tests — `backend/tests/unit/test_pagination.py`**

```python
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.pagination import SortField, SortSpec, decode_cursor, encode_cursor

TODO_ID = UUID("018f3b2c-0000-7000-8000-000000000001")


def test_parse_ascending_sort():
    spec = SortSpec.parse("due_date")
    assert spec.field is SortField.DUE_DATE
    assert spec.descending is False


def test_parse_descending_sort():
    spec = SortSpec.parse("-priority")
    assert spec.field is SortField.PRIORITY
    assert spec.descending is True


def test_parse_rejects_unknown_field():
    with pytest.raises(ValueError):
        SortSpec.parse("created_at")


def test_cursor_round_trips_a_datetime():
    value = datetime(2026, 6, 1, 9, tzinfo=UTC)
    decoded_value, decoded_id = decode_cursor(encode_cursor(value, TODO_ID))
    assert decoded_value == value
    assert decoded_id == TODO_ID


def test_cursor_round_trips_an_integer():
    decoded_value, decoded_id = decode_cursor(encode_cursor(20, TODO_ID))
    assert decoded_value == 20
    assert decoded_id == TODO_ID


def test_cursor_round_trips_a_string():
    decoded_value, decoded_id = decode_cursor(encode_cursor("write the plan", TODO_ID))
    assert decoded_value == "write the plan"
    assert decoded_id == TODO_ID


def test_cursor_is_url_safe():
    cursor = encode_cursor(datetime(2026, 6, 1, tzinfo=UTC), TODO_ID)
    assert "=" not in cursor
    assert "/" not in cursor
    assert "+" not in cursor


def test_decode_rejects_malformed_cursor():
    with pytest.raises(ValueError):
        decode_cursor("not-a-real-cursor")


def _cursor_from(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_decode_rejects_wrong_typed_value():
    """Decodes as valid JSON but int(None) would raise TypeError, not ValueError.

    Cursors arrive as query parameters, so the wrong exception type here is a 500.
    """
    with pytest.raises(ValueError):
        decode_cursor(_cursor_from({"t": "int", "v": None, "id": str(TODO_ID)}))


def test_decode_rejects_non_isoformat_datetime_value():
    with pytest.raises(ValueError):
        decode_cursor(_cursor_from({"t": "dt", "v": 123, "id": str(TODO_ID)}))


def test_decode_rejects_unknown_value_type():
    with pytest.raises(ValueError):
        decode_cursor(_cursor_from({"t": "blob", "v": "x", "id": str(TODO_ID)}))
```

These four rejection tests need `base64` and `json` imported at the top of the test
file alongside the existing imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/unit/test_pagination.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pagination'`.

- [ ] **Step 3: Create `backend/app/pagination.py`**

```python
"""Keyset pagination cursors. Pure — no database, no HTTP."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class SortField(StrEnum):
    DUE_DATE = "due_date"
    PRIORITY = "priority"
    STATUS = "status"
    NAME = "name"


@dataclass(frozen=True)
class SortSpec:
    field: SortField
    descending: bool

    @classmethod
    def parse(cls, raw: str) -> "SortSpec":
        descending = raw.startswith("-")
        name = raw[1:] if descending else raw
        try:
            field = SortField(name)
        except ValueError as exc:
            allowed = ", ".join(f.value for f in SortField)
            raise ValueError(f"Unknown sort field '{name}'. Allowed: {allowed}.") from exc
        return cls(field=field, descending=descending)


def encode_cursor(sort_value: Any, todo_id: UUID) -> str:
    if isinstance(sort_value, datetime):
        payload = {"t": "dt", "v": sort_value.isoformat(), "id": str(todo_id)}
    elif isinstance(sort_value, int):
        payload = {"t": "int", "v": sort_value, "id": str(todo_id)}
    else:
        payload = {"t": "str", "v": str(sort_value), "id": str(todo_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, UUID]:
    """Every failure mode must surface as ValueError.

    Type reconstruction stays INSIDE the try: a cursor that base64- and
    JSON-decodes cleanly can still carry a mismatched value type, and
    `int(None)` raises TypeError. Cursors arrive as query parameters, so an
    unhandled TypeError there is a 500 from user input.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        kind, value = payload["t"], payload["v"]
        todo_id = UUID(payload["id"])
        if kind == "dt":
            return datetime.fromisoformat(value), todo_id
        if kind == "int":
            return int(value), todo_id
        if kind == "str":
            return str(value), todo_id
    except Exception as exc:
        raise ValueError("Malformed pagination cursor.") from exc

    # Unknown discriminator: reject rather than silently coercing to str.
    raise ValueError(f"Unknown cursor value type: {kind!r}.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/unit/test_pagination.py -v`
Expected: PASS — 11 tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pagination.py backend/tests/unit/test_pagination.py
git commit -m "feat: add keyset pagination cursor encoding and sort parsing"
```

---

# Phase 3 — Persistence

### Task 5: ORM models and initial migration

**Files:**
- Create: `backend/app/models/__init__.py`, `backend/app/models/base.py`, `backend/app/models/todo.py`, `backend/app/models/dependency.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_initial.py`
- Test: `backend/tests/conftest.py`, `backend/tests/integration/test_models.py`

**Interfaces:**
- Consumes: `Status`, `Priority`, `RecurrenceUnit` from `app.domain.enums`.
- Produces: `Base`; `Todo` ORM model with the columns below; `TodoDependency` with `(todo_id, depends_on_id)`; pytest fixtures `session` (`AsyncSession`, rolled back per test) and `client` (`httpx.AsyncClient` bound to the app with `get_session` overridden).

- [ ] **Step 1: Create `backend/app/models/base.py`**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 2: Create `backend/app/models/todo.py`**

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, Integer, SmallInteger, String, text
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
```

- [ ] **Step 3: Create `backend/app/models/dependency.py`**

```python
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
```

- [ ] **Step 4: Initialise Alembic and write the initial migration**

Run: `cd backend && alembic init alembic`, then set `sqlalchemy.url` handling in `alembic/env.py` to read `app.config.settings.database_url` (stripping `+asyncpg` for the sync migration driver) and `target_metadata = Base.metadata`. Generate:

```bash
cd backend && alembic revision --autogenerate -m "initial schema" --rev-id 0001
```

Inspect the generated file. Confirm it creates both enum types, both tables, all six indexes, and all three check constraints. Fix by hand if autogenerate missed the partial-index `postgresql_where` clauses.

- [ ] **Step 5: Create `backend/tests/conftest.py`**

```python
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db import get_session
from app.main import create_app
from app.models.base import Base

test_engine = create_async_engine(settings.test_database_url)
TestSessionFactory = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema() -> AsyncIterator[None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


async def _truncate_all() -> None:
    async with TestSessionFactory() as s:
        for table in reversed(Base.metadata.sorted_tables):
            await s.execute(table.delete())
        await s.commit()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A direct session, for tests that exercise the models rather than the API."""
    async with TestSessionFactory() as s:
        yield s
    await _truncate_all()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """An API client where every request gets its OWN session.

    This matters: `asyncio.gather` over one shared AsyncSession raises
    "another operation is in progress" — asyncpg connections are not
    concurrency-safe. The concurrency tests are the centrepiece of this
    project, so requests must not share a session.
    """
    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await _truncate_all()
```

- [ ] **Step 6: Write the failing test — `backend/tests/integration/test_models.py`**

```python
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.domain.enums import Priority, Status
from app.models.todo import Todo


async def test_todo_defaults(session):
    todo = Todo(name="Write the plan")
    session.add(todo)
    await session.commit()

    stored = (await session.execute(select(Todo))).scalar_one()
    assert stored.status is Status.NOT_STARTED
    assert stored.priority == Priority.MEDIUM
    assert stored.version == 1
    assert stored.unmet_dependency_count == 0
    assert stored.deleted_at is None
    assert stored.id is not None


async def test_self_dependency_is_rejected_by_constraint(session):
    todo = Todo(name="A")
    session.add(todo)
    await session.commit()

    with pytest.raises(IntegrityError):
        await session.execute(
            text("INSERT INTO todo_dependencies (todo_id, depends_on_id) VALUES (:i, :i)"),
            {"i": todo.id},
        )
        await session.commit()


async def test_status_enum_sorts_in_lifecycle_order(session):
    """Sorting by status must be lifecycle order, not alphabetical."""
    for name, status in [
        ("d", Status.ARCHIVED),
        ("b", Status.IN_PROGRESS),
        ("a", Status.NOT_STARTED),
        ("c", Status.COMPLETED),
    ]:
        session.add(Todo(name=name, status=status))
    await session.commit()

    ordered = (await session.execute(select(Todo).order_by(Todo.status))).scalars().all()
    assert [t.name for t in ordered] == ["a", "b", "c", "d"]
```

- [ ] **Step 7: Run the tests**

Run: `cd backend && createdb todo_test 2>/dev/null; pytest tests/integration/test_models.py -v`
Expected: PASS — 3 tests. The lifecycle-order test is the one that matters; if it fails, the enum declaration order is wrong.

- [ ] **Step 8: Verify the migration applies cleanly from scratch**

Run: `cd backend && alembic upgrade head && alembic downgrade base && alembic upgrade head`
Expected: no errors in either direction.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models backend/alembic backend/alembic.ini backend/tests
git commit -m "feat: add Todo and TodoDependency models with initial migration"
```

---

# Phase 4 — API core

### Task 6: Problem Details error handling

**Files:**
- Create: `backend/app/schemas/__init__.py`, `backend/app/schemas/errors.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_errors.py`

**Interfaces:**
- Consumes: `DomainError` and subclasses from `app.errors`.
- Produces: `register_exception_handlers(app: FastAPI) -> None`; every error response carries `application/problem+json` with keys `type`, `title`, `status`, `detail`, `code`, plus any `extra`.

- [ ] **Step 1: Create `backend/app/schemas/errors.py`**

```python
from typing import Any

from pydantic import BaseModel


class ProblemDetail(BaseModel):
    """RFC 9457 Problem Details."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str
    errors: list[dict[str, Any]] | None = None
```

- [ ] **Step 2: Write the failing test — `backend/tests/integration/test_errors.py`**

The handlers are exercised through a probe app defined in the test file, not through
the todo routes — those arrive in Task 7, and a task's tests must pass when the task
lands. The probe routes exist only in this test module and never ship.

```python
import httpx
import pytest
from pydantic import BaseModel

from app.errors import BlockedByDependencies, NotFound
from app.main import create_app


def build_probe_app():
    """The real app factory plus throwaway routes that raise each error shape."""
    app = create_app()

    class Body(BaseModel):
        name: str

    @app.get("/_probe/not-found")
    async def raise_not_found():
        raise NotFound("No todo with id 42.")

    @app.get("/_probe/blocked")
    async def raise_blocked():
        raise BlockedByDependencies("Cannot start.", extra={"unmet_dependency_count": 3})

    @app.post("/_probe/validated")
    async def validated(body: Body):
        return {"ok": True}

    return app


@pytest.fixture
async def probe_client():
    transport = httpx.ASGITransport(app=build_probe_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_domain_error_becomes_problem_details(probe_client):
    response = await probe_client.get("/_probe/not-found")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["status"] == 404
    assert body["title"]
    assert body["detail"] == "No todo with id 42."


async def test_domain_error_extra_is_spread_into_the_body(probe_client):
    """`extra` carries the machine-readable payload clients act on."""
    response = await probe_client.get("/_probe/blocked")
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "BLOCKED_BY_DEPENDENCIES"
    assert body["unmet_dependency_count"] == 3


async def test_validation_failure_becomes_problem_details(probe_client):
    response = await probe_client.post("/_probe/validated", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in body["errors"]] == ["name"]
```

- [ ] **Step 3: Add handlers to `backend/app/main.py`**

Insert above `create_app`, then call `register_exception_handlers(app)` inside it:

```python
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import DomainError

PROBLEM_JSON = "application/problem+json"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            media_type=PROBLEM_JSON,
            content={
                "type": "about:blank",
                "title": exc.title,
                "status": exc.status_code,
                "detail": exc.detail,
                "code": exc.code,
                **exc.extra,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            media_type=PROBLEM_JSON,
            content={
                "type": "about:blank",
                "title": "Request validation failed",
                "status": 422,
                "detail": "One or more fields are invalid.",
                "code": "VALIDATION_ERROR",
                "errors": [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                    for e in exc.errors()
                ],
            },
        )
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && pytest tests/integration/test_errors.py -v`
Expected: PASS — 3 tests. The probe app makes this task self-contained; nothing here waits on Task 7.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas backend/app/main.py backend/tests/integration/test_errors.py
git commit -m "feat: return RFC 9457 Problem Details for domain and validation errors"
```

---

### Task 7: Todo CRUD with optimistic concurrency

**Files:**
- Create: `backend/app/schemas/todo.py`, `backend/app/repositories/__init__.py`, `backend/app/repositories/todo_repo.py`, `backend/app/services/__init__.py`, `backend/app/services/todo_service.py`, `backend/app/routers/todos.py`
- Modify: `backend/app/main.py` (mount `todos.router` at `/api`)
- Test: `backend/tests/integration/test_crud.py`, `backend/tests/integration/test_concurrency.py`

**Interfaces:**
- Consumes: `Todo`, `DomainError` subclasses, `Status`, `Priority`, `RecurrenceUnit`.
- Produces:
  - Schemas: `TodoCreate`, `TodoUpdate`, `TodoRead` (includes `version`, `is_blocked`, `unmet_dependency_count`, `depends_on: list[UUID]`).
  - `TodoRepository(session)` with `get(todo_id, *, include_deleted=False) -> Todo | None`, `insert(todo) -> Todo`, `update_versioned(todo_id, expected_version, values) -> Todo | None`, `soft_delete(todo_id, expected_version) -> Todo | None`, `restore(todo_id) -> Todo | None`.
  - `TodoService(session)` with `create`, `get`, `update`, `delete`, `restore`.
  - `require_if_match(request) -> int` dependency — raises `PreconditionRequired` when absent, `VersionConflict` when unparseable.

**Design note:** `update_versioned` issues a single `UPDATE ... WHERE id = :id AND version = :expected ... RETURNING *`. Zero rows means either the row is gone or someone else wrote first; the service then reads the row to decide between `404` and `409`. Returning the current state in the `409` body lets the UI show the user what changed instead of just failing.

- [ ] **Step 1: Create `backend/app/schemas/todo.py`**

```python
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
```

- [ ] **Step 2: Write the failing tests — `backend/tests/integration/test_crud.py`**

```python
async def create_todo(client, **overrides):
    payload = {"name": "Write the spec", "priority": "high"} | overrides
    response = await client.post("/api/todos", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_returns_todo_with_version_one(client):
    todo = await create_todo(client)
    assert todo["name"] == "Write the spec"
    assert todo["priority"] == "high"
    assert todo["status"] == "not_started"
    assert todo["version"] == 1
    assert todo["is_blocked"] is False


async def test_create_rejects_empty_name(client):
    response = await client.post("/api/todos", json={"name": ""})
    assert response.status_code == 422


async def test_unknown_todo_returns_problem_details(client):
    """Task 6's handlers, now exercised through a real route."""
    response = await client.get("/api/todos/018f3b2c-0000-7000-8000-0000000000ff")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_FOUND"


async def test_create_rejects_recurrence_without_due_date(client):
    response = await client.post(
        "/api/todos",
        json={"name": "Standup", "recurrence_unit": "day", "recurrence_interval": 1},
    )
    assert response.status_code == 422


async def test_get_returns_etag(client):
    todo = await create_todo(client)
    response = await client.get(f"/api/todos/{todo['id']}")
    assert response.status_code == 200
    assert response.headers["etag"] == '"1"'


async def test_patch_increments_version(client):
    todo = await create_todo(client)
    response = await client.patch(
        f"/api/todos/{todo['id']}",
        json={"name": "Renamed"},
        headers={"If-Match": '"1"'},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["version"] == 2


async def test_patch_without_if_match_returns_428(client):
    todo = await create_todo(client)
    response = await client.patch(f"/api/todos/{todo['id']}", json={"name": "Renamed"})
    assert response.status_code == 428
    assert response.json()["code"] == "PRECONDITION_REQUIRED"


async def test_delete_is_soft_and_hides_the_todo(client):
    todo = await create_todo(client)
    assert (await client.delete(f"/api/todos/{todo['id']}", headers={"If-Match": '"1"'})).status_code == 204
    assert (await client.get(f"/api/todos/{todo['id']}")).status_code == 404
    # ...but the row survives and is reachable explicitly.
    found = await client.get(f"/api/todos/{todo['id']}", params={"include_deleted": True})
    assert found.status_code == 200
    assert found.json()["deleted_at"] is not None


async def test_restore_brings_a_deleted_todo_back(client):
    todo = await create_todo(client)
    await client.delete(f"/api/todos/{todo['id']}", headers={"If-Match": '"1"'})
    restored = await client.post(f"/api/todos/{todo['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert (await client.get(f"/api/todos/{todo['id']}")).status_code == 200
```

- [ ] **Step 3: Write the failing tests — `backend/tests/integration/test_concurrency.py`**

```python
import asyncio


async def test_stale_version_is_rejected_with_409(client):
    todo = (await client.post("/api/todos", json={"name": "Shared task"})).json()
    first = await client.patch(
        f"/api/todos/{todo['id']}", json={"name": "User A"}, headers={"If-Match": '"1"'}
    )
    assert first.status_code == 200

    second = await client.patch(
        f"/api/todos/{todo['id']}", json={"name": "User B"}, headers={"If-Match": '"1"'}
    )
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "VERSION_CONFLICT"
    # The conflict body must carry current state so the UI can show what changed.
    assert body["current"]["name"] == "User A"
    assert body["current"]["version"] == 2


async def test_concurrent_updates_produce_exactly_one_winner(client):
    todo = (await client.post("/api/todos", json={"name": "Contended"})).json()
    responses = await asyncio.gather(
        *[
            client.patch(
                f"/api/todos/{todo['id']}",
                json={"name": f"writer-{i}"},
                headers={"If-Match": '"1"'},
            )
            for i in range(5)
        ]
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 1
    assert codes.count(409) == 4
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && pytest tests/integration/test_crud.py tests/integration/test_concurrency.py -v`
Expected: FAIL — all routes return 404, the routers do not exist yet.

- [ ] **Step 5: Create `backend/app/repositories/todo_repo.py`**

```python
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.todo import Todo


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
        self, todo_id: UUID, expected_version: int, values: dict[str, Any]
    ) -> Todo | None:
        """Single-statement compare-and-set. None means lost race or row gone."""
        stmt = (
            update(Todo)
            .where(
                Todo.id == todo_id,
                Todo.version == expected_version,
                Todo.deleted_at.is_(None),
            )
            .values(**values, version=Todo.version + 1, updated_at=datetime.now(UTC))
            .returning(Todo)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def soft_delete(self, todo_id: UUID, expected_version: int) -> Todo | None:
        return await self.update_versioned(todo_id, expected_version, {"deleted_at": datetime.now(UTC)})

    async def restore(self, todo_id: UUID) -> Todo | None:
        stmt = (
            update(Todo)
            .where(Todo.id == todo_id, Todo.deleted_at.is_not(None))
            .values(deleted_at=None, version=Todo.version + 1, updated_at=datetime.now(UTC))
            .returning(Todo)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
```

- [ ] **Step 6: Create `backend/app/services/todo_service.py`**

```python
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFound, VersionConflict
from app.models.todo import Todo
from app.repositories.todo_repo import TodoRepository
from app.schemas.todo import NAME_TO_PRIORITY, TodoCreate, TodoRead, TodoUpdate


class TodoService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TodoRepository(session)

    async def create(self, payload: TodoCreate) -> Todo:
        todo = Todo(
            name=payload.name,
            description=payload.description,
            due_date=payload.due_date,
            priority=int(NAME_TO_PRIORITY[payload.priority]),
            recurrence_unit=payload.recurrence_unit,
            recurrence_interval=payload.recurrence_interval,
        )
        if payload.recurrence_unit is not None:
            todo.recurrence_series_id = uuid4()
            todo.recurrence_anchor_due = payload.due_date
            todo.occurrence_index = 0
        todo = await self.repo.insert(todo)
        await self.session.commit()
        return todo

    async def get(self, todo_id: UUID, *, include_deleted: bool = False) -> Todo:
        todo = await self.repo.get(todo_id, include_deleted=include_deleted)
        if todo is None:
            raise NotFound(f"No todo with id {todo_id}.")
        return todo

    async def update(self, todo_id: UUID, expected_version: int, payload: TodoUpdate) -> Todo:
        values = payload.model_dump(exclude_unset=True)
        if "priority" in values:
            if values["priority"] not in NAME_TO_PRIORITY:
                raise ValueError("priority must be one of: low, medium, high")
            values["priority"] = int(NAME_TO_PRIORITY[values["priority"]])

        updated = await self.repo.update_versioned(todo_id, expected_version, values)
        if updated is None:
            await self._raise_conflict_or_not_found(todo_id)
        await self.session.commit()
        return updated

    async def delete(self, todo_id: UUID, expected_version: int) -> Todo:
        deleted = await self.repo.soft_delete(todo_id, expected_version)
        if deleted is None:
            await self._raise_conflict_or_not_found(todo_id)
        await self.session.commit()
        return deleted

    async def restore(self, todo_id: UUID) -> Todo:
        restored = await self.repo.restore(todo_id)
        if restored is None:
            raise NotFound(f"No deleted todo with id {todo_id}.")
        await self.session.commit()
        return restored

    async def _raise_conflict_or_not_found(self, todo_id: UUID) -> None:
        """Distinguish 'someone else wrote first' from 'it is not there'."""
        current = await self.repo.get(todo_id)
        if current is None:
            raise NotFound(f"No todo with id {todo_id}.")
        raise VersionConflict(
            "This todo was modified by someone else. Reload and retry.",
            extra={"current": TodoRead.from_todo(current).model_dump(mode="json")},
        )
```

- [ ] **Step 7: Create `backend/app/routers/todos.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import PreconditionRequired, VersionConflict
from app.schemas.todo import TodoCreate, TodoRead, TodoUpdate
from app.services.todo_service import TodoService

router = APIRouter(prefix="/api/todos", tags=["todos"])


def require_if_match(request: Request) -> int:
    raw = request.headers.get("if-match")
    if raw is None:
        raise PreconditionRequired("This request requires an If-Match header carrying the version.")
    try:
        return int(raw.strip().strip("W/").strip('"'))
    except ValueError as exc:
        raise VersionConflict(f"Malformed If-Match value: {raw!r}.") from exc


def _with_etag(response: Response, todo) -> TodoRead:
    response.headers["ETag"] = f'"{todo.version}"'
    return TodoRead.from_todo(todo)


@router.post("", response_model=TodoRead, status_code=status.HTTP_201_CREATED)
async def create_todo(
    payload: TodoCreate, response: Response, session: AsyncSession = Depends(get_session)
) -> TodoRead:
    return _with_etag(response, await TodoService(session).create(payload))


@router.get("/{todo_id}", response_model=TodoRead)
async def get_todo(
    todo_id: UUID,
    response: Response,
    include_deleted: bool = False,
    session: AsyncSession = Depends(get_session),
) -> TodoRead:
    todo = await TodoService(session).get(todo_id, include_deleted=include_deleted)
    return _with_etag(response, todo)


@router.patch("/{todo_id}", response_model=TodoRead)
async def update_todo(
    todo_id: UUID,
    payload: TodoUpdate,
    response: Response,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
) -> TodoRead:
    todo = await TodoService(session).update(todo_id, expected_version, payload)
    return _with_etag(response, todo)


@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(
    todo_id: UUID,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await TodoService(session).delete(todo_id, expected_version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{todo_id}/restore", response_model=TodoRead)
async def restore_todo(
    todo_id: UUID, response: Response, session: AsyncSession = Depends(get_session)
) -> TodoRead:
    return _with_etag(response, await TodoService(session).restore(todo_id))
```

- [ ] **Step 8: Mount the router in `backend/app/main.py`**

Add `from app.routers import todos` and `app.include_router(todos.router)` inside `create_app`.

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd backend && pytest tests/integration/test_crud.py tests/integration/test_concurrency.py tests/integration/test_errors.py -v`
Expected: PASS — all tests, including the two concurrency tests and both error tests from Task 6.

- [ ] **Step 10: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: add todo CRUD with soft delete and optimistic concurrency"
```

---

### Task 8: Listing — filtering, sorting, keyset pagination

**Files:**
- Modify: `backend/app/repositories/todo_repo.py` (add `list_page`), `backend/app/services/todo_service.py` (add `list_todos`), `backend/app/routers/todos.py` (add `GET /api/todos`)
- Test: `backend/tests/integration/test_listing.py`

**Interfaces:**
- Consumes: `SortSpec`, `SortField`, `encode_cursor`, `decode_cursor`, `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE`.
- Produces: `TodoFilter` dataclass (`statuses`, `priorities`, `due_before`, `due_after`, `blocked`, `include_deleted`); `TodoRepository.list_page(filters, sort, cursor, limit) -> tuple[list[Todo], str | None]`.

**Design note — nullable sort keys:** row-value comparison `(sort_col, id) > (:value, :id)` is how keyset paging stays index-friendly, but it breaks on `NULL`. `due_date` is nullable, so the sort expression is `COALESCE(due_date, '9999-12-31')` for ascending (nulls last) and `COALESCE(due_date, '0001-01-01')` for descending (nulls last again). Every sort expression is therefore non-null, and the comparison is valid.

- [ ] **Step 1: Write the failing tests — `backend/tests/integration/test_listing.py`**

```python
from datetime import UTC, datetime, timedelta

NOW = datetime(2026, 6, 1, tzinfo=UTC)


async def seed(client, count=5):
    created = []
    for i in range(count):
        response = await client.post(
            "/api/todos",
            json={
                "name": f"task-{i:02d}",
                "priority": ["low", "medium", "high"][i % 3],
                "due_date": (NOW + timedelta(days=i)).isoformat(),
            },
        )
        created.append(response.json())
    return created


async def test_list_returns_page_with_cursor(client):
    await seed(client, 5)
    response = await client.get("/api/todos", params={"limit": 2, "sort": "name"})
    assert response.status_code == 200
    body = response.json()
    assert [t["name"] for t in body["items"]] == ["task-00", "task-01"]
    assert body["next_cursor"] is not None


async def test_cursor_walks_the_whole_list_without_gaps_or_repeats(client):
    await seed(client, 7)
    seen, cursor = [], None
    while True:
        params = {"limit": 2, "sort": "name"}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get("/api/todos", params=params)).json()
        seen.extend(t["name"] for t in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(seen)
    assert len(seen) == len(set(seen)) == 7


async def test_sort_descending(client):
    await seed(client, 3)
    body = (await client.get("/api/todos", params={"sort": "-name"})).json()
    assert [t["name"] for t in body["items"]] == ["task-02", "task-01", "task-00"]


async def test_sort_by_priority_is_semantic_not_alphabetical(client):
    await seed(client, 3)
    body = (await client.get("/api/todos", params={"sort": "-priority"})).json()
    assert body["items"][0]["priority"] == "high"


async def test_filter_by_priority(client):
    await seed(client, 6)
    body = (await client.get("/api/todos", params={"priority": "high"})).json()
    assert len(body["items"]) == 2
    assert all(t["priority"] == "high" for t in body["items"])


async def test_filter_by_due_date_range(client):
    await seed(client, 5)
    body = (
        await client.get(
            "/api/todos", params={"due_before": (NOW + timedelta(days=2)).isoformat()}
        )
    ).json()
    assert len(body["items"]) == 2


async def test_deleted_todos_are_excluded_by_default(client):
    todos = await seed(client, 3)
    await client.delete(f"/api/todos/{todos[0]['id']}", headers={"If-Match": '"1"'})
    assert len((await client.get("/api/todos")).json()["items"]) == 2
    assert len((await client.get("/api/todos", params={"include_deleted": True})).json()["items"]) == 3


async def test_unknown_sort_field_is_rejected(client):
    response = await client.get("/api/todos", params={"sort": "created_at"})
    assert response.status_code == 422


async def test_limit_is_capped(client):
    response = await client.get("/api/todos", params={"limit": 5000})
    assert response.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/integration/test_listing.py -v`
Expected: FAIL — `GET /api/todos` is not routed yet.

- [ ] **Step 3: Add `list_page` to `backend/app/repositories/todo_repo.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, func, tuple_

from app.domain.enums import Status
from app.pagination import SortField, SortSpec, decode_cursor, encode_cursor

DATE_MAX = datetime(9999, 12, 31, tzinfo=UTC)
DATE_MIN = datetime(1, 1, 1, tzinfo=UTC)


@dataclass
class TodoFilter:
    statuses: list[Status] = field(default_factory=list)
    priorities: list[int] = field(default_factory=list)
    due_before: datetime | None = None
    due_after: datetime | None = None
    blocked: bool | None = None
    include_deleted: bool = False


def sort_expression(sort: SortSpec):
    """Always non-null, so row-value comparison in the keyset predicate is valid."""
    if sort.field is SortField.DUE_DATE:
        return func.coalesce(Todo.due_date, DATE_MIN if sort.descending else DATE_MAX)
    if sort.field is SortField.PRIORITY:
        return Todo.priority
    if sort.field is SortField.STATUS:
        return Todo.status
    return Todo.name
```

Then, as a method on `TodoRepository`:

```python
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
            row = tuple_(key, Todo.id)
            anchor = tuple_(last_value, last_id)
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
```

- [ ] **Step 4: Add `list_todos` to `TodoService`**

```python
    async def list_todos(
        self, filters: TodoFilter, sort: SortSpec, cursor: str | None, limit: int
    ) -> tuple[list[Todo], str | None]:
        return await self.repo.list_page(filters, sort, cursor, limit)
```

- [ ] **Step 5: Add the route to `backend/app/routers/todos.py`**

```python
from datetime import datetime

from fastapi import Query
from fastapi.exceptions import RequestValidationError

from app.domain.enums import Status
from app.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SortSpec
from app.repositories.todo_repo import TodoFilter
from app.schemas.todo import NAME_TO_PRIORITY, TodoPage


@router.get("", response_model=TodoPage)
async def list_todos(
    status_filter: list[Status] = Query(default=[], alias="status"),
    priority: list[str] = Query(default=[]),
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    blocked: bool | None = None,
    include_deleted: bool = False,
    sort: str = "due_date",
    cursor: str | None = None,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    session: AsyncSession = Depends(get_session),
) -> TodoPage:
    try:
        sort_spec = SortSpec.parse(sort)
        priorities = [int(NAME_TO_PRIORITY[p]) for p in priority]
    except (ValueError, KeyError) as exc:
        raise RequestValidationError([{"loc": ("query", "sort"), "msg": str(exc)}]) from exc

    filters = TodoFilter(
        statuses=status_filter,
        priorities=priorities,
        due_before=due_before,
        due_after=due_after,
        blocked=blocked,
        include_deleted=include_deleted,
    )
    items, next_cursor = await TodoService(session).list_todos(filters, sort_spec, cursor, limit)
    return TodoPage(items=[TodoRead.from_todo(t) for t in items], next_cursor=next_cursor)
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && pytest tests/integration/test_listing.py -v`
Expected: PASS — 9 tests. Filtering by status is exercised in Task 10, where the status endpoint exists; nothing here is left failing or marked `xfail`.

- [ ] **Step 7: Commit**

```bash
git add backend/app backend/tests/integration/test_listing.py
git commit -m "feat: add filtering, sorting, and keyset pagination to todo listing"
```

---

# Phase 5 — Dependencies and recurrence

### Task 9: Dependency edges with cycle detection

**Files:**
- Create: `backend/app/repositories/dependency_repo.py`, `backend/app/services/dependency_service.py`, `backend/app/routers/dependencies.py`
- Modify: `backend/app/main.py`, `backend/app/services/todo_service.py` (load `depends_on` in `get`)
- Test: `backend/tests/integration/test_dependencies.py`

**Interfaces:**
- Consumes: `DependencyCycle`, `NotFound`, `TodoRepository`.
- Produces:
  - `DependencyRepository(session)` with `add(todo_id, depends_on_id)`, `remove(todo_id, depends_on_id) -> bool`, `list_for(todo_id) -> list[UUID]`, `find_cycle_path(todo_id, depends_on_id) -> list[UUID] | None`, `dependents_of(todo_id) -> list[UUID]`, `recompute_counts(todo_ids)`.
  - `DependencyService(session)` with `add_dependency`, `remove_dependency`.

**Design note — cycle detection:** before inserting edge `A → B`, the question is whether `A` is already reachable from `B` by following `depends_on` edges. A recursive CTE answers it in one round trip and returns the offending path, which goes into the error body. Tracking the visited path in an array also stops the CTE looping forever on a graph that is already cyclic.

- [ ] **Step 1: Write the failing tests — `backend/tests/integration/test_dependencies.py`**

```python
async def make(client, name):
    return (await client.post("/api/todos", json={"name": name})).json()


async def link(client, todo, depends_on):
    return await client.post(
        f"/api/todos/{todo['id']}/dependencies", json={"depends_on_id": depends_on["id"]}
    )


async def test_adding_a_dependency_blocks_the_dependent(client):
    a, b = await make(client, "A"), await make(client, "B")
    assert (await link(client, a, b)).status_code == 201

    refreshed = (await client.get(f"/api/todos/{a['id']}")).json()
    assert refreshed["unmet_dependency_count"] == 1
    assert refreshed["is_blocked"] is True
    assert refreshed["depends_on"] == [b["id"]]


async def test_recomputing_counts_does_not_bump_version(client):
    """Derived state must not invalidate clients' versions."""
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    assert (await client.get(f"/api/todos/{a['id']}")).json()["version"] == 1


async def test_direct_cycle_is_rejected(client):
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    response = await link(client, b, a)
    assert response.status_code == 422
    assert response.json()["code"] == "DEPENDENCY_CYCLE"


async def test_multi_hop_cycle_is_rejected(client):
    a, b, c = await make(client, "A"), await make(client, "B"), await make(client, "C")
    await link(client, a, b)
    await link(client, b, c)
    response = await link(client, c, a)
    assert response.status_code == 422
    assert response.json()["code"] == "DEPENDENCY_CYCLE"
    assert len(response.json()["cycle_path"]) >= 3


async def test_self_dependency_is_rejected(client):
    a = await make(client, "A")
    response = await link(client, a, a)
    assert response.status_code == 422


async def test_duplicate_dependency_is_idempotent(client):
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    assert (await link(client, a, b)).status_code in (200, 201)
    assert (await client.get(f"/api/todos/{a['id']}")).json()["unmet_dependency_count"] == 1


async def test_removing_a_dependency_unblocks(client):
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    response = await client.delete(f"/api/todos/{a['id']}/dependencies/{b['id']}")
    assert response.status_code == 204
    assert (await client.get(f"/api/todos/{a['id']}")).json()["is_blocked"] is False


async def test_dependency_on_unknown_todo_returns_404(client):
    a = await make(client, "A")
    response = await client.post(
        f"/api/todos/{a['id']}/dependencies",
        json={"depends_on_id": "018f3b2c-0000-7000-8000-0000000000ff"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/integration/test_dependencies.py -v`
Expected: FAIL — dependency routes do not exist.

- [ ] **Step 3: Create `backend/app/repositories/dependency_repo.py`**

```python
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dependency import TodoDependency

CYCLE_PROBE = text("""
WITH RECURSIVE reachable(id, path) AS (
    SELECT depends_on_id, ARRAY[todo_id, depends_on_id]
    FROM todo_dependencies
    WHERE todo_id = :start
  UNION ALL
    SELECT d.depends_on_id, r.path || d.depends_on_id
    FROM todo_dependencies d
    JOIN reachable r ON d.todo_id = r.id
    WHERE NOT d.depends_on_id = ANY(r.path)
)
SELECT path FROM reachable WHERE id = :target LIMIT 1
""")

RECOMPUTE_COUNTS = text("""
UPDATE todos t
SET unmet_dependency_count = (
    SELECT count(*)
    FROM todo_dependencies d
    JOIN todos dep ON dep.id = d.depends_on_id
    WHERE d.todo_id = t.id
      AND dep.status <> 'completed'
      AND dep.deleted_at IS NULL
)
WHERE t.id = ANY(:ids)
""")


class DependencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, todo_id: UUID, depends_on_id: UUID) -> None:
        stmt = (
            pg_insert(TodoDependency)
            .values(todo_id=todo_id, depends_on_id=depends_on_id)
            .on_conflict_do_nothing()
        )
        await self.session.execute(stmt)

    async def remove(self, todo_id: UUID, depends_on_id: UUID) -> bool:
        result = await self.session.execute(
            delete(TodoDependency).where(
                TodoDependency.todo_id == todo_id,
                TodoDependency.depends_on_id == depends_on_id,
            )
        )
        return result.rowcount > 0

    async def list_for(self, todo_id: UUID) -> list[UUID]:
        stmt = select(TodoDependency.depends_on_id).where(TodoDependency.todo_id == todo_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def dependents_of(self, todo_id: UUID) -> list[UUID]:
        stmt = select(TodoDependency.todo_id).where(TodoDependency.depends_on_id == todo_id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_cycle_path(self, todo_id: UUID, depends_on_id: UUID) -> list[UUID] | None:
        """Would `todo_id -> depends_on_id` close a loop? Returns the path if so."""
        if todo_id == depends_on_id:
            return [todo_id, depends_on_id]
        result = await self.session.execute(
            CYCLE_PROBE, {"start": depends_on_id, "target": todo_id}
        )
        row = result.first()
        return list(row[0]) if row else None

    async def recompute_counts(self, todo_ids: list[UUID]) -> None:
        """Refresh derived state. Deliberately does not touch `version`."""
        if todo_ids:
            await self.session.execute(RECOMPUTE_COUNTS, {"ids": todo_ids})
```

- [ ] **Step 4: Create `backend/app/services/dependency_service.py`**

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DependencyCycle, NotFound
from app.repositories.dependency_repo import DependencyRepository
from app.repositories.todo_repo import TodoRepository


class DependencyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.deps = DependencyRepository(session)
        self.todos = TodoRepository(session)

    async def add_dependency(self, todo_id: UUID, depends_on_id: UUID) -> None:
        for candidate in (todo_id, depends_on_id):
            if await self.todos.get(candidate) is None:
                raise NotFound(f"No todo with id {candidate}.")

        cycle = await self.deps.find_cycle_path(todo_id, depends_on_id)
        if cycle is not None:
            raise DependencyCycle(
                "This dependency would create a cycle.",
                extra={"cycle_path": [str(i) for i in cycle]},
            )

        await self.deps.add(todo_id, depends_on_id)
        await self.deps.recompute_counts([todo_id])
        await self.session.commit()

    async def remove_dependency(self, todo_id: UUID, depends_on_id: UUID) -> None:
        if not await self.deps.remove(todo_id, depends_on_id):
            raise NotFound(f"Todo {todo_id} does not depend on {depends_on_id}.")
        await self.deps.recompute_counts([todo_id])
        await self.session.commit()
```

- [ ] **Step 5: Create `backend/app/routers/dependencies.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services.dependency_service import DependencyService

router = APIRouter(prefix="/api/todos", tags=["dependencies"])


class DependencyCreate(BaseModel):
    depends_on_id: UUID


@router.post("/{todo_id}/dependencies", status_code=status.HTTP_201_CREATED)
async def add_dependency(
    todo_id: UUID, payload: DependencyCreate, session: AsyncSession = Depends(get_session)
) -> Response:
    await DependencyService(session).add_dependency(todo_id, payload.depends_on_id)
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete("/{todo_id}/dependencies/{depends_on_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_dependency(
    todo_id: UUID, depends_on_id: UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    await DependencyService(session).remove_dependency(todo_id, depends_on_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 6: Populate `depends_on` in the single-todo response**

In `backend/app/routers/todos.py`, change `get_todo` to load the edge list:

```python
@router.get("/{todo_id}", response_model=TodoRead)
async def get_todo(
    todo_id: UUID,
    response: Response,
    include_deleted: bool = False,
    session: AsyncSession = Depends(get_session),
) -> TodoRead:
    todo = await TodoService(session).get(todo_id, include_deleted=include_deleted)
    depends_on = await DependencyRepository(session).list_for(todo_id)
    response.headers["ETag"] = f'"{todo.version}"'
    return TodoRead.from_todo(todo, depends_on=depends_on)
```

- [ ] **Step 7: Mount the router**

Add `from app.routers import dependencies` and `app.include_router(dependencies.router)` in `create_app`.

- [ ] **Step 8: Run the tests**

Run: `cd backend && pytest tests/integration/test_dependencies.py -v`
Expected: PASS — 8 tests.

- [ ] **Step 9: Commit**

```bash
git add backend/app backend/tests/integration/test_dependencies.py
git commit -m "feat: add task dependencies with recursive-CTE cycle detection"
```

---

### Task 10: Status transitions, recurrence spawning, and count propagation

**Files:**
- Create: `backend/app/services/status_service.py`
- Modify: `backend/app/routers/todos.py` (add `POST /{todo_id}/status`), `backend/app/services/todo_service.py` (propagate counts on delete/restore), `backend/app/schemas/todo.py` (add `StatusChange`, `StatusChangeResult`)
- Test: `backend/tests/integration/test_status.py`

**Interfaces:**
- Consumes: `validate_transition`, `next_occurrence`, `DependencyRepository.dependents_of`, `DependencyRepository.recompute_counts`, `TodoRepository.update_versioned`.
- Produces: `StatusService(session).change_status(todo_id, expected_version, target) -> tuple[Todo, Todo | None]`; schemas `StatusChange(status: Status)` and `StatusChangeResult(todo: TodoRead, next_occurrence: TodoRead | None)`.

**Design note — idempotency comes free.** Completing a recurring todo spawns the next occurrence, and the whole operation runs inside the versioned update. Two concurrent completions cannot both succeed, because the second fails the compare-and-set and returns `409`. No separate idempotency key is needed — one mechanism covers both lost updates and duplicate spawning.

- [ ] **Step 1: Add the schemas to `backend/app/schemas/todo.py`**

```python
class StatusChange(BaseModel):
    status: Status


class StatusChangeResult(BaseModel):
    todo: TodoRead
    next_occurrence: TodoRead | None = None
```

- [ ] **Step 2: Write the failing tests — `backend/tests/integration/test_status.py`**

```python
import asyncio
from datetime import UTC, datetime, timedelta

FUTURE = datetime(2030, 1, 31, 9, tzinfo=UTC)


async def make(client, **overrides):
    payload = {"name": "task"} | overrides
    return (await client.post("/api/todos", json=payload)).json()


async def set_status(client, todo, status, version=1):
    return await client.post(
        f"/api/todos/{todo['id']}/status",
        json={"status": status},
        headers={"If-Match": f'"{version}"'},
    )


async def test_status_change_increments_version(client):
    todo = await make(client)
    response = await set_status(client, todo, "in_progress")
    assert response.status_code == 200
    assert response.json()["todo"]["status"] == "in_progress"
    assert response.json()["todo"]["version"] == 2


async def test_blocked_todo_cannot_start(client):
    a, b = await make(client, name="A"), await make(client, name="B")
    await client.post(f"/api/todos/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    response = await set_status(client, a, "in_progress")
    assert response.status_code == 422
    assert response.json()["code"] == "BLOCKED_BY_DEPENDENCIES"


async def test_blocked_todo_cannot_jump_straight_to_completed(client):
    a, b = await make(client, name="A"), await make(client, name="B")
    await client.post(f"/api/todos/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    assert (await set_status(client, a, "completed")).status_code == 422


async def test_completing_a_dependency_unblocks_its_dependents(client):
    a, b = await make(client, name="A"), await make(client, name="B")
    await client.post(f"/api/todos/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    await set_status(client, b, "completed")
    assert (await client.get(f"/api/todos/{a['id']}")).json()["is_blocked"] is False
    assert (await set_status(client, a, "in_progress")).status_code == 200


async def test_archiving_is_allowed_while_blocked(client):
    a, b = await make(client, name="A"), await make(client, name="B")
    await client.post(f"/api/todos/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    assert (await set_status(client, a, "archived")).status_code == 200


async def test_soft_deleting_a_dependency_unblocks_its_dependents(client):
    """Spec 2.6 — a deleted blocker must not block forever."""
    a, b = await make(client, name="A"), await make(client, name="B")
    await client.post(f"/api/todos/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    await client.delete(f"/api/todos/{b['id']}", headers={"If-Match": '"1"'})
    assert (await client.get(f"/api/todos/{a['id']}")).json()["is_blocked"] is False


async def test_restoring_a_dependency_reblocks_its_dependents(client):
    a, b = await make(client, name="A"), await make(client, name="B")
    await client.post(f"/api/todos/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    await client.delete(f"/api/todos/{b['id']}", headers={"If-Match": '"1"'})
    await client.post(f"/api/todos/{b['id']}/restore")
    assert (await client.get(f"/api/todos/{a['id']}")).json()["is_blocked"] is True


async def test_completing_a_recurring_todo_spawns_the_next_occurrence(client):
    todo = await make(
        client,
        name="Pay rent",
        due_date=FUTURE.isoformat(),
        recurrence_unit="month",
        recurrence_interval=1,
    )
    response = await set_status(client, todo, "completed")
    assert response.status_code == 200

    body = response.json()
    assert body["todo"]["status"] == "completed"
    spawned = body["next_occurrence"]
    assert spawned is not None
    assert spawned["name"] == "Pay rent"
    assert spawned["status"] == "not_started"
    assert spawned["recurrence_series_id"] == body["todo"]["recurrence_series_id"]
    # 31 Jan + 1 month clamps to 28 Feb.
    assert spawned["due_date"].startswith("2030-02-28")


async def test_spawned_occurrence_does_not_inherit_dependencies(client):
    """Spec 2.7 — copied edges would point at completed todos anyway."""
    blocker = await make(client, name="Blocker")
    todo = await make(
        client,
        name="Weekly report",
        due_date=FUTURE.isoformat(),
        recurrence_unit="week",
        recurrence_interval=1,
    )
    await client.post(f"/api/todos/{todo['id']}/dependencies", json={"depends_on_id": blocker["id"]})
    await set_status(client, blocker, "completed")

    body = (await set_status(client, todo, "completed")).json()
    spawned = (await client.get(f"/api/todos/{body['next_occurrence']['id']}")).json()
    assert spawned["depends_on"] == []


async def test_completing_a_non_recurring_todo_spawns_nothing(client):
    todo = await make(client, due_date=FUTURE.isoformat())
    assert (await set_status(client, todo, "completed")).json()["next_occurrence"] is None


async def test_concurrent_completions_spawn_exactly_one_occurrence(client):
    todo = await make(
        client,
        name="Standup",
        due_date=FUTURE.isoformat(),
        recurrence_unit="day",
        recurrence_interval=1,
    )
    responses = await asyncio.gather(
        *[set_status(client, todo, "completed") for _ in range(4)]
    )
    assert sorted(r.status_code for r in responses).count(200) == 1

    listing = (await client.get("/api/todos", params={"limit": 200})).json()
    assert len(listing["items"]) == 2  # the original plus exactly one spawned occurrence


async def test_transition_to_current_status_is_rejected(client):
    todo = await make(client)
    assert (await set_status(client, todo, "not_started")).status_code == 422


async def test_filter_by_status(client):
    """Deferred from Task 8 — the status endpoint only exists here."""
    for name in ("a", "b", "c"):
        await make(client, name=name)
    listing = (await client.get("/api/todos", params={"limit": 200})).json()
    await set_status(client, listing["items"][0], "in_progress")

    filtered = (await client.get("/api/todos", params={"status": "in_progress"})).json()
    assert len(filtered["items"]) == 1
    assert filtered["items"][0]["status"] == "in_progress"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && pytest tests/integration/test_status.py -v`
Expected: FAIL — the status route does not exist.

- [ ] **Step 4: Create `backend/app/services/status_service.py`**

```python
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import Status
from app.domain.recurrence import next_occurrence
from app.domain.transitions import validate_transition
from app.errors import NotFound, VersionConflict
from app.models.todo import Todo
from app.repositories.dependency_repo import DependencyRepository
from app.repositories.todo_repo import TodoRepository
from app.schemas.todo import TodoRead


class StatusService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.todos = TodoRepository(session)
        self.deps = DependencyRepository(session)

    async def change_status(
        self, todo_id: UUID, expected_version: int, target: Status
    ) -> tuple[Todo, Todo | None]:
        current = await self.todos.get(todo_id)
        if current is None:
            raise NotFound(f"No todo with id {todo_id}.")

        validate_transition(current.status, target, current.unmet_dependency_count)

        values: dict = {"status": target}
        values["completed_at"] = datetime.now(UTC) if target is Status.COMPLETED else None

        updated = await self.todos.update_versioned(todo_id, expected_version, values)
        if updated is None:
            # Someone else moved first — the compare-and-set is also what stops
            # two concurrent completions both spawning an occurrence.
            fresh = await self.todos.get(todo_id)
            if fresh is None:
                raise NotFound(f"No todo with id {todo_id}.")
            raise VersionConflict(
                "This todo was modified by someone else. Reload and retry.",
                extra={"current": TodoRead.from_todo(fresh).model_dump(mode="json")},
            )

        spawned = None
        if target is Status.COMPLETED and updated.recurrence_unit is not None:
            spawned = await self._spawn_next(updated)

        # Completing or reopening changes whether this todo satisfies its dependents.
        await self.deps.recompute_counts(await self.deps.dependents_of(todo_id))
        await self.session.commit()
        return updated, spawned

    async def _spawn_next(self, completed: Todo) -> Todo:
        anchor = completed.recurrence_anchor_due or completed.due_date
        due, index = next_occurrence(
            anchor=anchor,
            unit=completed.recurrence_unit,
            interval=completed.recurrence_interval,
            current_index=completed.occurrence_index,
            now=datetime.now(UTC),
        )
        # Dependencies are deliberately not copied (spec 2.7).
        return await self.todos.insert(
            Todo(
                name=completed.name,
                description=completed.description,
                due_date=due,
                status=Status.NOT_STARTED,
                priority=completed.priority,
                recurrence_unit=completed.recurrence_unit,
                recurrence_interval=completed.recurrence_interval,
                recurrence_series_id=completed.recurrence_series_id,
                recurrence_anchor_due=anchor,
                occurrence_index=index,
            )
        )
```

- [ ] **Step 5: Add the route to `backend/app/routers/todos.py`**

```python
from app.schemas.todo import StatusChange, StatusChangeResult
from app.services.status_service import StatusService


@router.post("/{todo_id}/status", response_model=StatusChangeResult)
async def change_status(
    todo_id: UUID,
    payload: StatusChange,
    response: Response,
    expected_version: int = Depends(require_if_match),
    session: AsyncSession = Depends(get_session),
) -> StatusChangeResult:
    todo, spawned = await StatusService(session).change_status(
        todo_id, expected_version, payload.status
    )
    response.headers["ETag"] = f'"{todo.version}"'
    return StatusChangeResult(
        todo=TodoRead.from_todo(todo),
        next_occurrence=TodoRead.from_todo(spawned) if spawned else None,
    )
```

- [ ] **Step 6: Propagate counts on delete and restore**

Add `from app.repositories.dependency_repo import DependencyRepository` to the imports of
`backend/app/services/todo_service.py`. Then in `TodoService.delete` and
`TodoService.restore`, before `await self.session.commit()`, add:

```python
        deps = DependencyRepository(self.session)
        await deps.recompute_counts(await deps.dependents_of(todo_id))
```

This is what makes a deleted blocker stop blocking, and a restored one start again (spec §2.6).

- [ ] **Step 7: Run the full suite**

Run: `cd backend && pytest -v`
Expected: PASS — the whole suite, every commit on this branch green.

- [ ] **Step 8: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: add status transitions with recurrence spawning and count propagation"
```

---

### Task 11: 10,000-item seed and performance verification

**Files:**
- Create: `backend/seed.py`, `docs/performance.md`
- Test: manual measurement, recorded in `docs/performance.md`

**Interfaces:**
- Consumes: `Todo`, `TodoDependency`, `Status`, `Priority`, `RecurrenceUnit`.
- Produces: `python -m seed --count 10000` populating the dev database.

**Design note:** "handles 10,000+ items" is a verifiable claim, so verify it. Unproven performance claims are exactly what a reviewer probes in the demo.

- [ ] **Step 1: Create `backend/seed.py`**

```python
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
    rng = random.Random(42)  # Deterministic: reruns produce the same dataset.
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
```

- [ ] **Step 2: Run the seed**

Run: `cd backend && python -m seed --count 10000`
Expected: `Seeded 10000 todos and ~4500 dependency edges.`

- [ ] **Step 3: Measure the four listings that matter**

Run each and record the wall time:

```bash
time curl -s "localhost:8000/api/todos?limit=50&sort=due_date" > /dev/null
time curl -s "localhost:8000/api/todos?limit=50&sort=-priority&status=in_progress" > /dev/null
time curl -s "localhost:8000/api/todos?limit=50&blocked=true" > /dev/null
time curl -s "localhost:8000/api/todos?limit=50&sort=name&cursor=<cursor from page 100>" > /dev/null
```

- [ ] **Step 4: Confirm the indexes are actually used**

Run in `psql`:

```sql
EXPLAIN ANALYZE
SELECT * FROM todos
WHERE deleted_at IS NULL AND unmet_dependency_count > 0
ORDER BY name, id LIMIT 51;
```

Expected: an **Index Scan**, not a Seq Scan. If it is a Seq Scan, the partial index predicate does not match the query predicate — fix the index before moving on.

- [ ] **Step 5: Write `docs/performance.md`**

Record: dataset size, the four measured timings, the `EXPLAIN ANALYZE` plan for the blocked filter, and one sentence on why keyset paging keeps page 100 as fast as page 1. This is source material for the decision log.

- [ ] **Step 6: Commit**

```bash
git add backend/seed.py docs/performance.md
git commit -m "feat: add 10k seed script and record measured query performance"
```

---

# Phase 6 — Web UI

### Task 12: Frontend scaffold, typed API client, list view

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/todos.ts`, `frontend/src/components/TodoList.tsx`, `frontend/src/components/FilterBar.tsx`

**Interfaces:**
- Consumes: the API from Tasks 7–10.
- Produces: `apiFetch<T>(path, init?) -> Promise<T>` throwing `ApiError` (carrying `code`, `status`, `problem`); hooks `useTodos(filters)`, `useCreateTodo()`, `useUpdateTodo()`, `useDeleteTodo()`, `useRestoreTodo()`, `useChangeStatus()`, `useAddDependency()`, `useRemoveDependency()`.

**Scope note:** automated frontend tests are a deliberate cut. The assignment asks for tests of core functionality; that logic lives in the backend and is covered there. This is recorded in the decision log rather than left unsaid.

- [ ] **Step 1: Scaffold**

```bash
cd frontend && npm create vite@latest . -- --template react-ts && npm install @tanstack/react-query
```

- [ ] **Step 2: Create `frontend/src/api/client.ts`**

```typescript
const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

export interface Problem {
  title: string;
  status: number;
  detail: string;
  code: string;
  errors?: { field: string; message: string }[];
  current?: Todo;
  cycle_path?: string[];
}

export class ApiError extends Error {
  constructor(readonly problem: Problem) {
    super(problem.detail);
  }
  get code() { return this.problem.code; }
  get status() { return this.problem.status; }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });

  if (!response.ok) {
    throw new ApiError(await response.json());
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

/** Every mutation must carry the version it read. */
export function ifMatch(version: number): Record<string, string> {
  return { "If-Match": `"${version}"` };
}
```

- [ ] **Step 3: Create `frontend/src/api/types.ts`**

```typescript
export type Status = "not_started" | "in_progress" | "completed" | "archived";
export type Priority = "low" | "medium" | "high";
export type RecurrenceUnit = "day" | "week" | "month";

export interface Todo {
  id: string;
  name: string;
  description: string | null;
  due_date: string | null;
  status: Status;
  priority: Priority;
  recurrence_unit: RecurrenceUnit | null;
  recurrence_interval: number | null;
  recurrence_series_id: string | null;
  unmet_dependency_count: number;
  is_blocked: boolean;
  depends_on: string[];
  version: number;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TodoPage {
  items: Todo[];
  next_cursor: string | null;
}

export interface TodoFilters {
  status?: Status[];
  priority?: Priority[];
  due_before?: string;
  due_after?: string;
  blocked?: boolean;
  include_deleted?: boolean;
  sort?: string;
}
```

- [ ] **Step 4: Create `frontend/src/api/todos.ts`**

```typescript
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch, ifMatch } from "./client";
import type { Status, Todo, TodoFilters, TodoPage } from "./types";

const KEY = "todos";

function toQuery(filters: TodoFilters, cursor?: string): string {
  const params = new URLSearchParams();
  filters.status?.forEach((s) => params.append("status", s));
  filters.priority?.forEach((p) => params.append("priority", p));
  if (filters.due_before) params.set("due_before", filters.due_before);
  if (filters.due_after) params.set("due_after", filters.due_after);
  if (filters.blocked !== undefined) params.set("blocked", String(filters.blocked));
  if (filters.include_deleted) params.set("include_deleted", "true");
  params.set("sort", filters.sort ?? "due_date");
  params.set("limit", "50");
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

export function useTodos(filters: TodoFilters) {
  return useInfiniteQuery({
    queryKey: [KEY, filters],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => apiFetch<TodoPage>(`/api/todos?${toQuery(filters, pageParam)}`),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: [KEY] });
}

export function useCreateTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: Partial<Todo>) =>
      apiFetch<Todo>("/api/todos", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: invalidate,
  });
}

export function useUpdateTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todo, changes }: { todo: Todo; changes: Partial<Todo> }) =>
      apiFetch<Todo>(`/api/todos/${todo.id}`, {
        method: "PATCH",
        headers: ifMatch(todo.version),
        body: JSON.stringify(changes),
      }),
    onSuccess: invalidate,
  });
}

export function useChangeStatus() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todo, status }: { todo: Todo; status: Status }) =>
      apiFetch<{ todo: Todo; next_occurrence: Todo | null }>(`/api/todos/${todo.id}/status`, {
        method: "POST",
        headers: ifMatch(todo.version),
        body: JSON.stringify({ status }),
      }),
    onSuccess: invalidate,
  });
}

export function useDeleteTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (todo: Todo) =>
      apiFetch<void>(`/api/todos/${todo.id}`, { method: "DELETE", headers: ifMatch(todo.version) }),
    onSuccess: invalidate,
  });
}

export function useRestoreTodo() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (todo: Todo) =>
      apiFetch<Todo>(`/api/todos/${todo.id}/restore`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

export function useAddDependency() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todoId, dependsOnId }: { todoId: string; dependsOnId: string }) =>
      apiFetch<void>(`/api/todos/${todoId}/dependencies`, {
        method: "POST",
        body: JSON.stringify({ depends_on_id: dependsOnId }),
      }),
    onSuccess: invalidate,
  });
}

export function useRemoveDependency() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ todoId, dependsOnId }: { todoId: string; dependsOnId: string }) =>
      apiFetch<void>(`/api/todos/${todoId}/dependencies/${dependsOnId}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}
```

- [ ] **Step 5: Build `FilterBar.tsx` and `TodoList.tsx`**

`FilterBar` renders: status multi-select, priority multi-select, a blocked/unblocked/any tri-state, a "show deleted" toggle, and a sort dropdown (`due_date`, `-due_date`, `priority`, `-priority`, `status`, `name`). It holds no state of its own — it takes `filters` and `onChange` props.

`TodoList` renders one row per todo showing name, status, priority, due date, and a "Blocked (N)" badge when `is_blocked`. It renders a "Load more" button while `hasNextPage`, calling `fetchNextPage`. Deleted rows render dimmed with a Restore button. The list is paged, never fully materialised — this is what satisfies the 10,000-item requirement on the client side.

- [ ] **Step 6: Verify against the seeded database**

Run: `docker compose up -d && cd frontend && npm run dev`, then load `http://localhost:5173` against the 10k dataset.
Expected: the first page renders promptly; filter and sort changes refetch from the server; "Load more" walks pages without duplicates.

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: add React frontend with typed API client and paged todo list"
```

---

### Task 13: Create/edit form, status control, dependency picker, conflict handling

**Files:**
- Create: `frontend/src/components/TodoForm.tsx`, `frontend/src/components/StatusControl.tsx`, `frontend/src/components/DependencyPicker.tsx`, `frontend/src/components/ConflictBanner.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: every hook from Task 12; `ApiError` from `client.ts`.
- Produces: the complete UI.

**Design note — the conflict path is the demo.** When a mutation rejects with `VERSION_CONFLICT`, the `409` body carries `current`. `ConflictBanner` shows what the other user changed and offers "Reload and reapply". Two browser windows editing one todo is the clearest way to demonstrate the concurrency requirement live.

- [ ] **Step 1: Build `TodoForm.tsx`**

Fields: name (required), description, due date (`datetime-local`), priority select, and a recurrence group (unit select + interval number) that is disabled unless a due date is set — mirroring the server-side rule that a recurring todo needs an anchor. Used for both create and edit; in edit mode it passes the todo's `version` through `useUpdateTodo`.

Render `error.problem.errors` inline against the matching field when the server returns `VALIDATION_ERROR`, so server-side validation is visible rather than silent.

- [ ] **Step 2: Build `StatusControl.tsx`**

A dropdown of the four statuses calling `useChangeStatus`. When the response carries a `next_occurrence`, surface a toast naming the spawned task and its new due date — this is what makes recurrence visible in the demo rather than an invisible row appearing somewhere in the list.

On `BLOCKED_BY_DEPENDENCIES`, show the returned `detail` and the blocking count rather than a generic failure.

- [ ] **Step 3: Build `DependencyPicker.tsx`**

A searchable select over existing todos calling `useAddDependency`, plus a list of current dependencies each with a remove button. On `DEPENDENCY_CYCLE`, render the returned `cycle_path` as a readable chain so the user can see which link closes the loop.

- [ ] **Step 4: Build `ConflictBanner.tsx`**

Rendered when a mutation fails with `VERSION_CONFLICT`. Shows the conflicting fields from `problem.current`, and a "Reload" button that invalidates the query and reopens the form against fresh data.

- [ ] **Step 5: Verify the full flow manually**

Check each: create → edit → delete → restore; filter and sort; add a dependency and confirm the dependent cannot start; complete the dependency and confirm it unblocks; complete a recurring todo and confirm the spawned occurrence appears with the expected due date; open two browser windows, edit the same todo in both, and confirm the second shows the conflict banner.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat: add todo form, status control, dependency picker, and conflict handling"
```

---

# Phase 7 — Documentation

### Task 14: README, decision log, architecture diagram

**Files:**
- Create: `README.md`, `docs/decision-log.md`, `docs/architecture.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-08-05-sleekflow-todo-design.md`, `docs/performance.md`.
- Produces: the assignment's deliverables 2 and 4.

- [ ] **Step 1: Write `README.md`**

Sections: what this is; quick start (`docker compose up`, then `python -m seed --count 10000`); local development for backend and frontend separately; running tests (`pytest`); where the API docs are (`http://localhost:8000/docs`); a feature checklist marking what is implemented and what is deliberately not; and a link to the decision log.

- [ ] **Step 2: Write `docs/architecture.md`**

A Mermaid diagram of browser → React SPA → FastAPI (routers/services/repositories) → PostgreSQL, plus a short paragraph on the request path for a status change, since that is the one operation touching every layer.

- [ ] **Step 3: Write `docs/decision-log.md` (1–2 pages)**

Four sections matching the assignment's required bullets exactly:

1. **Ambiguities and how they were resolved** — draw from spec §2: shared list vs. per-user, archived vs. deleted, "custom" recurrence as unit+interval, recurrence anchoring and month-end clamping, the `→ completed` bypass, deletion of a blocker.
2. **Architectural decisions and trade-offs** — optimistic concurrency over pessimistic locking; the denormalised `unmet_dependency_count` (write cost traded for read cost, and why the read side wins here); keyset over offset pagination; a dedicated status endpoint over `PATCH`; anchor+index recurrence over previous-due+interval, with the drift example; Vite over Next.js.
3. **What was not built and why** — spec §9, including the frontend-test cut.
4. **What would be done differently with more time** — SSE for real-time; an outbox or audit table for full history rather than only soft delete; RRULE if real users needed complex schedules; contract tests generated from the OpenAPI schema.

Keep it to two pages. Reviewers read this closely; length is not the same as substance.

- [ ] **Step 4: Verify the quick start on a clean checkout**

Run: `git clone <repo> /tmp/verify && cd /tmp/verify && docker compose up -d && sleep 15 && curl -s localhost:8000/health`
Expected: `{"status":"ok"}` with no manual steps beyond what the README lists. If a step is missing from the README, add it now — this is the exact path the interviewer will follow.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/
git commit -m "docs: add README, architecture diagram, and decision log"
```

---

## Stretch tasks (only if ahead of schedule)

Ordered by signal per hour. Each is independent; stop at any point.

### Stretch A: GitHub Actions running the test suite (~20 min)

`.github/workflows/ci.yml` — a Postgres service container, `pip install -e ".[dev]"`, `alembic upgrade head`, `pytest`, `ruff check`. Ticks the DevOps nice-to-have for very little effort, and a green badge in the README is visible in the first ten seconds of a repo review.

### Stretch B: Bulk status endpoint (~1 h)

`POST /api/todos/bulk-status` taking `{ ids: UUID[], status: Status }` and returning per-item results, so a partial failure (one blocked item) does not fail the batch. Reuses `StatusService.change_status` per item inside one transaction.

### Stretch C: SSE real-time updates (~2 h)

`GET /api/events` streaming `text/event-stream`, with the frontend calling `queryClient.invalidateQueries` on each event. Chosen over WebSockets because updates are one-directional and SSE needs no extra infrastructure or protocol upgrade handling.

---

## Spec amendment

Task 2 changes the recurrence model from the spec as written. Spec §2.4 says "next due = previous due + interval". Computing each occurrence from the previous one drifts off month ends: 31 Jan → 28 Feb → 28 Mar, when it should be 31 Mar.

The plan therefore stores `recurrence_anchor_due` and `occurrence_index` on each row and computes occurrence *n* as `anchor + n × interval`, clamping fresh each time. Update spec §2.4 and §4.1 to match before starting Task 2.
