# SleekFlow TODO

A shared TODO list web application: a FastAPI backend (async SQLAlchemy 2.0 + PostgreSQL) and a
React + Vite + TypeScript frontend. One board, many users. Signing in gates access and names who
changed what — it does **not** scope data, and there is no `owner_id`. Concurrency is therefore
answered by optimistic versioning rather than by ownership (see the
[decision log](docs/decision-log.md) for the reasoning).

**Features.** Standard CRUD; a status workflow (`not_started` → `in_progress` → `completed`, plus a
reversible `archived`); recurring tasks (daily / weekly / monthly / custom, spawn next occurrence on
completion); task dependencies with cycle rejection and blocked/unblocked enforcement; soft delete
with restore; optimistic concurrency (`If-Match` + version, `409` on conflict); server-side
filtering and sorting with keyset pagination; and a functional web UI. Plus all three
nice-to-haves: accounts with JWT bearer tokens, live updates across tabs over server-sent events,
and batch operations with per-item results. Designed and measured to stay fast with 10,000+ items
([docs/performance.md](docs/performance.md)).

**Assignment deliverables.** Setup and local development instructions (this file), API docs
(auto-generated Swagger at `http://localhost:8000/docs`), a [decision log](docs/decision-log.md),
and an [architecture diagram](docs/architecture.md).

## Repository layout

```
backend/    FastAPI + SQLAlchemy 2.0 + asyncpg + Alembic
frontend/   React + Vite + TypeScript + TanStack Query
docs/       decision-log.md, architecture.md, performance.md
docker-compose.yml
```

## Quick start (Docker)

Requires Docker with Compose. Nothing else — the API container runs migrations on startup.

```bash
git clone <this-repo>
cd <repo>
docker compose up -d --build
```

Migrations run automatically as part of the API container's start (`alembic upgrade head`). When
the API is up:

```bash
curl localhost:8000/health        # {"status":"ok"}
```

API docs: <http://localhost:8000/docs>.

**Seed the demo dataset** (recommended before the demo; the app works empty too):

```bash
docker compose exec api python -m seed --count 10000
```

This creates 10,000 todos with a realistic dependency graph. Run it once — re-running appends
another batch.

**Create the test database once** (needed by `pytest`, section below):

```bash
docker compose exec db psql -U todo -c "CREATE DATABASE todo_test"
```

**Web UI:**

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. The UI talks to `http://localhost:8000` directly (CORS is
preconfigured); set `VITE_API_BASE` if your API runs elsewhere.

### If port 5432 is taken

A native PostgreSQL (Windows installer, Homebrew, another container) can own port 5432 and the
container then cannot bind there. The compose file publishes Postgres on `${DB_PORT:-5432}`:

```bash
echo "DB_PORT=5433" > .env      # in the project root, next to docker-compose.yml
docker compose up -d --build    # db is now published on localhost:5433
```

Host-side tools (local uvicorn, alembic, pytest) must then point at 5433 — edit
`backend/.env.local` and change `5432` to `5433`.

## Local development

### Backend

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate      Unix/macOS: source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db                 # Postgres must be reachable on the port in .env.local
alembic upgrade head
uvicorn app.main:app --reload           # http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                             # http://localhost:5173
```

## Running tests

```bash
cd backend
pytest -q
```

173 tests: pure unit tests over recurrence date math (including month-end clamping), cycle
detection, and every status transition; integration tests exercise the real API against a real
Postgres, including concurrent-write tests that assert exactly one writer wins, batch operations
that assert a refused item fails alone, and event-stream tests that assert every mutating route
announces itself. Requires the
`todo_test` database (created in the quick start above) and `backend/.env.local` pointing at the
running Postgres.

## API documentation

Swagger UI at <http://localhost:8000/docs>, generated from the OpenAPI schema (also at
`/openapi.json`). Every mutation requires an `If-Match` header carrying the row's version (returned
as an `ETag`); a stale write gets `409 Conflict` with the current server state so the client can
recover. Errors are RFC 9457 Problem Details with machine-readable codes.

## Feature checklist

Implemented:

- [x] CRUD with validation (RFC 9457 Problem Details errors)
- [x] Status workflow `not_started` → `in_progress` → `completed`; `archived` reversible
- [x] Recurring tasks: daily / weekly / monthly / custom (unit + interval); next occurrence spawned
      on completion; month-end clamping; no backlog when completed late
- [x] Dependencies: cycle rejection; blocked enforcement on both `→ in_progress` and
      `→ completed`; deleting a blocker unblocks dependents
- [x] Soft delete + restore (trash view via `include_deleted`)
- [x] Optimistic concurrency: `If-Match` version check, `409` with current state
- [x] Filtering (status, priority, due window, blocked) and sorting (due date, priority, status,
      name), server-side
- [x] Keyset pagination — page 100 costs the same as page 1 (measured)
- [x] 10,000+ item performance verified by measurement — [docs/performance.md](docs/performance.md)
- [x] React UI: paged list, filter/sort controls, create/edit, dependency picker, status
      transitions, delete/restore, conflict surfacing
- [x] Tests: 173 passing

Nice-to-haves from the assignment:

- [x] User authentication and registration — username + password, bcrypt, 12-hour JWT bearer
      tokens, declared as an OpenAPI security scheme. A gate and an identity, not ownership: the
      board stays shared, and every change records who made it
- [x] Real-time updates across browser tabs or users — `GET /api/events`, a server-sent event
      stream. Each frame names the action, the todo and the actor; clients re-read rather than
      patch their cache from it
- [x] Bulk operations — `POST /api/todos/bulk/status` and `/bulk/delete`, with per-item results
      so one blocked or stale todo does not fail the selection. Multi-select and a batch action
      bar in the UI. Each item carries its own version, so optimistic concurrency survives
      batching

Deliberately not built (each with its rationale in the [decision log](docs/decision-log.md)):

- [ ] Per-user todo ownership — contradicts "the same TODO list", and would kill the concurrency
      story since two users would rarely touch the same row
- [ ] iCal RRULE recurrence — unit + interval covers all four stated cases
- [ ] Cascading un-complete
- [ ] Tags, subtasks, comments, attachments, full-text search
