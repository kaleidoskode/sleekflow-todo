# SleekFlow TODO Application — Design Spec

**Date:** 2026-08-05
**Status:** Approved, ready for implementation planning
**Time budget:** 2–3 days

---

## 1. Objective

Build a shared TODO list application — REST API plus a functional web UI — covering
CRUD, recurring tasks, task dependencies, and filtering/sorting, against three
non-functional requirements: concurrent multi-user access, no permanent data loss on
delete, and usable performance at 10,000+ items.

The assignment states it contains more requirements than can be completed in a
reasonable timeframe, and that prioritization and communication are themselves graded.
This spec therefore commits to a fixed core, an explicit cut list, and a stretch
ordering — rather than attempting everything.

---

## 2. Requirement interpretation

The assignment is deliberately underspecified in several places. Each resolution below
is a decision, not a discovery, and is carried into the decision log.

### 2.1 Ownership: one shared list, no authentication

The requirement reads "multiple users accessing **the same** TODO list concurrently,"
while authentication is listed only as a nice-to-have. Read literally, this is a single
shared list with no per-user ownership.

"Concurrently" is therefore about **lost-update protection**, not about identity. Two
users editing the same row is the scenario to handle, and the answer is optimistic
concurrency control (§4.1) — not login.

Per-user private lists were rejected: they contradict the wording and would make the
concurrency requirement nearly vacuous, since two users would rarely touch the same row.

### 2.2 Archived vs. deleted

`Archived` already exists as a status, while a separate requirement says deleted data
must not be permanently lost. These are two different concepts and are modelled
separately:

- **Archived** — a lifecycle *status* the user chooses. Still listed when filtered for.
- **Deleted** — `deleted_at` soft delete. Hidden from all normal queries, restorable,
  visible only through an explicit trash view.

### 2.3 "Custom" recurrence

The four stated options (daily, weekly, monthly, custom) are modelled as
**unit + interval**:

| Stated option | Model |
|---|---|
| daily | `(day, 1)` |
| weekly | `(week, 1)` |
| monthly | `(month, 1)` |
| custom | any other pair — every 3 days, every 2 weeks |

Full iCal RRULE (RFC 5545) is **cut**. "Every 2nd Tuesday", end dates, and occurrence
counts are out of scope. RRULE is a multi-day feature on its own and is not required to
satisfy any of the four stated cases.

### 2.4 Recurrence anchoring

Next due date = previous due date + interval. If the result is still in the past
(because the task was completed late), advance repeatedly until it lands in the future.

This keeps fixed-cadence tasks on their true schedule, while avoiding the spawning of
multiple backdated occurrences when a task is completed weeks overdue.

Monthly recurrence clamps to the last valid day of the target month — 31 Jan + 1 month
= 28/29 Feb.

### 2.5 The Completed bypass hole

The assignment blocks only the transition to `In Progress` until dependencies are
complete. Left as written, a caller could move a blocked task straight to `Completed`
and bypass the feature entirely.

The dependency rule is therefore enforced on **both** `→ in_progress` and
`→ completed`. This is a deliberate departure from the literal text.

### 2.6 Deleting a todo that others depend on

Deletion is allowed. The deleted todo is dropped from its dependents' blocking
calculation, so they unblock immediately rather than being blocked forever by a task
nobody can see. Dependency rows are preserved, so restoring re-establishes the block.

The stricter alternative — refusing to delete anything with dependents — is more
rigorous but frustrating in practice.

### 2.7 What a spawned occurrence inherits

A new occurrence copies name, description, priority, due-date offset, and the recurrence
rule, and shares the `recurrence_series_id`. It does **not** copy dependency edges, and
starts unblocked.

Copying edges would point the new occurrence at todos that are already `completed` —
satisfied by definition, so the count would be zero regardless. Not copying reaches the
same state with less noise in the graph.

### 2.8 Smaller resolutions

- **Priority** is `e.g.` in the assignment, so it is ours to define: three levels,
  stored as an ordered integer, exposed as a string. Storing the string directly would
  make "sort by priority" alphabetical.
- **Due date is nullable.** Nothing requires one, and a todo without a deadline is normal.
- **Sort by status** uses the logical lifecycle order, not alphabetical.
- **Archived is reversible.** Nothing indicates it is terminal.
- **Reopening a completed dependency does not cascade** to its dependents. Cascading is
  surprising and was not asked for.

---

## 3. Architecture

### 3.1 Stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | FastAPI, async SQLAlchemy 2.0, asyncpg | Pydantic covers the input-validation requirement; OpenAPI docs are generated, satisfying a deliverable at no cost |
| Database | PostgreSQL | The dependency graph and blocked/unblocked filter want recursive CTEs and partial indexes |
| Migrations | Alembic | Explicit versioned migrations rather than `create_all()` |
| Frontend | React + Vite + TypeScript + TanStack Query | No SSR tier is needed against a separate API backend; TanStack Query supplies cache invalidation and makes conflict handling demonstrable |
| Packaging | Docker Compose | Single `docker compose up`, satisfying "easily run and tested locally" |

Next.js was rejected: its value is its server tier, which would be redundant against a
separate FastAPI backend, and would add a second runtime to keep alive during the demo.

### 3.2 Repository layout

```
/backend      FastAPI application
/frontend     React + Vite SPA
/docs         decision-log.md, architecture diagram
docker-compose.yml
```

### 3.3 Backend layering

```
routers  →  services  →  repositories  →  PostgreSQL
```

- **Routers** own HTTP only: request/response shape and status codes. Thin.
- **Services** own domain rules — cycle detection, the blocking rule, recurrence date
  math, status transitions.
- **Repositories** own queries, pagination, and the soft-delete filter.
- **Pydantic schemas** at the boundary; **SQLAlchemy models** never leak out of a route.

The purpose of this split is testability: the three non-trivial behaviours (cycles,
blocking, recurrence date math) become pure functions over plain data, testable with no
HTTP layer and no database.

---

## 4. Data model

### 4.1 `todos`

| Column | Type | Notes |
|---|---|---|
| `id` | UUIDv7 | Time-ordered: indexes well, works as a keyset tiebreaker |
| `name` | text, not null | |
| `description` | text, null | |
| `due_date` | timestamptz, null | |
| `status` | enum | `not_started` / `in_progress` / `completed` / `archived` |
| `priority` | smallint | 10/20/30, exposed as `low`/`medium`/`high` |
| `recurrence_unit` | enum, null | `day` / `week` / `month` |
| `recurrence_interval` | int, null | |
| `recurrence_series_id` | uuid, null | Groups occurrences of one series |
| `unmet_dependency_count` | int, not null | Denormalized; see §5.3 |
| `version` | int, not null | Optimistic concurrency |
| `deleted_at` | timestamptz, null | Soft delete |
| `created_at` / `updated_at` / `completed_at` | timestamptz | |

### 4.2 `todo_dependencies`

Composite primary key `(todo_id, depends_on_id)`, `CHECK (todo_id <> depends_on_id)`,
and an index on `depends_on_id` for reverse lookups during count recomputation.

### 4.3 Status state machine

| From | To | Guard |
|---|---|---|
| `not_started` | `in_progress` | All dependencies `completed` |
| `not_started` | `completed` | All dependencies `completed` (§2.5) |
| `in_progress` | `completed` | — |
| `completed` | `not_started` / `in_progress` | Reopen; no downstream cascade |
| any | `archived` | — |
| `archived` | any | Unarchive allowed |

A recurrence occurrence is spawned **only** on a transition *into* `completed`.

---

## 5. Non-functional requirements

### 5.1 Concurrent access — optimistic concurrency

Every mutation carries the row's `version` via an `If-Match` header. The write executes
as `UPDATE ... WHERE id = ? AND version = ?`; zero affected rows means another writer
won, and the response is **409 Conflict** carrying the current server state so the
client can surface the difference.

This same mechanism supplies **recurrence idempotency**: two concurrent "complete this
recurring task" requests cannot both spawn an occurrence, because the second fails the
version check. One mechanism, two problems.

### 5.2 No permanent data loss — soft delete

`deleted_at` with a repository-level filter applied globally. `DELETE` sets it,
`POST /todos/{id}/restore` clears it, and a trash view surfaces deleted rows. Distinct
from the `archived` status per §2.2.

### 5.3 10,000+ items

In order of importance:

1. **`unmet_dependency_count` maintained transactionally.** The blocked/unblocked filter
   is the hidden performance trap: computed naively it is a correlated subquery per row.
   Maintaining the count whenever a dependency's status changes or an edge is added or
   removed reduces the filter to `WHERE unmet_dependency_count > 0` — a single indexed
   predicate. The same column makes the blocking guard a field read rather than a graph
   walk on every transition.
2. **Keyset pagination**, not `OFFSET`, which degrades linearly.
3. **Indexes matched to the actual filter/sort combinations**, with
   `WHERE deleted_at IS NULL` partial indexes.
4. **The UI never loads the full list** — filtering and sorting are server-side.

A seed script generates 10,000+ todos with a realistic dependency graph. The performance
claim is verifiable, so it will be verified and the measured timings recorded in the
decision log.

---

## 6. API surface

Base path `/api`. FastAPI's generated `/docs` satisfies the OpenAPI deliverable.

### 6.1 Todos

```
GET    /api/todos               list: filter, sort, keyset pagination
POST   /api/todos               create
GET    /api/todos/{id}          single, with dependencies and blocked state
PATCH  /api/todos/{id}          partial field edit            [If-Match]
DELETE /api/todos/{id}          soft delete                   [If-Match]
POST   /api/todos/{id}/restore  undelete
POST   /api/todos/{id}/status   transition                    [If-Match]
```

Status changes deliberately get a dedicated endpoint rather than going through `PATCH`.
A transition is not a field write: it validates the dependency rule, may spawn a
recurrence occurrence, and recomputes dependent counts. A dedicated endpoint makes those
semantics explicit and allows the response to return `{ todo, next_occurrence }`, so
completing a recurring task returns the task it created.

### 6.2 Dependencies

```
POST   /api/todos/{id}/dependencies             { depends_on_id }
DELETE /api/todos/{id}/dependencies/{dep_id}
```

### 6.3 List query parameters

`status[]`, `priority[]`, `due_before`, `due_after`, `blocked` (bool),
`include_deleted` (bool), `sort` (`due_date` / `priority` / `status` / `name`, `-`
prefix for descending), `cursor`, `limit`.

Response: `{ items, next_cursor }`.

### 6.4 Errors

RFC 9457 Problem Details (`application/problem+json`) throughout, with machine-readable
codes:

| Status | Code | Meaning |
|---|---|---|
| 404 | — | Not found, or soft-deleted without `include_deleted` |
| 409 | `VERSION_CONFLICT` | Body carries current server state |
| 422 | `DEPENDENCY_CYCLE` | Rejected edge, with the cycle path |
| 422 | `BLOCKED_BY_DEPENDENCIES` | Transition refused, listing the blocking todos |
| 428 | `PRECONDITION_REQUIRED` | `If-Match` absent on a mutation |

Returning *which* dependencies block a transition, rather than a bare rejection, is the
difference between an API that can be built against and one that cannot.

---

## 7. Testing strategy

**Unit tests** over the pure domain functions, which is where the edge cases live:

- Recurrence date math — month-end clamping (31 Jan → 28/29 Feb), roll-forward past
  multiple missed intervals, every unit/interval combination.
- Cycle detection — direct (A→A, rejected by constraint), two-node (A→B→A), and
  multi-hop (A→B→C→A).
- Status transitions — every edge of the state machine in §4.3, including the blocked
  cases and the `→ completed` guard.

**Integration tests** over the paths that only exist end-to-end:

- Two simultaneous updates to one row produce exactly one success and one 409.
- Two simultaneous completions of one recurring todo spawn exactly one occurrence.
- Soft delete removes a row from default listings, restore returns it.
- Deleting a dependency unblocks its dependents; restoring re-blocks them.
- Filter and sort combinations return correct results and paginate correctly.

---

## 8. Delivery plan

### Day 1 — the spine

Repository scaffold and `git init`, Docker Compose (postgres + api + web), Alembic
migrations, models, full CRUD with validation and Problem Details errors, the list
endpoint with filtering/sorting/keyset pagination, soft delete and restore, optimistic
concurrency end to end, and the 10k seed script.

*Checkpoint: the API is fully usable through Swagger.*

### Day 2 — domain logic

Dependencies with cycle detection, `unmet_dependency_count` maintenance and the blocked
filter, the status state machine with blocking enforcement, and recurrence. Then the
test suite described in §7.

*Checkpoint: backend feature-complete.*

### Day 3 — UI, documentation, proof

React UI: paged list, filter and sort controls, create/edit form, dependency picker,
status transitions, delete and restore, and 409 conflict handling surfaced properly.
Then README, decision log, architecture diagram, and the 10k performance measurement.

The UI is scheduled last deliberately. The assignment states it need not be polished,
and a complete backend with a plain UI demonstrates more than a polished UI over
half-built domain logic.

---

## 9. Explicitly out of scope

| Cut | Rationale |
|---|---|
| Authentication and registration | Contradicts "the same TODO list"; concurrency is answered by versioning, not identity |
| iCal RRULE recurrence | Multi-day feature; unit + interval covers all four stated cases |
| Real-time updates | Design documented (SSE over WebSockets — one-directional, no extra infrastructure), not built |
| Bulk operations | Endpoint shape sketched in the decision log, not implemented |
| Cascading un-complete | Surprising behaviour, not requested |
| Full-text search, tags, subtasks, comments, attachments | Not in the assignment |

**Stretch ordering if ahead of schedule**, cheapest signal first:

1. GitHub Actions running the test suite — roughly 20 minutes, satisfies the DevOps
   nice-to-have.
2. Bulk status endpoint.
3. SSE real-time updates.

---

## 10. Deliverables mapping

| Required deliverable | Where satisfied |
|---|---|
| GitHub repository | Repository root |
| README with setup instructions | `README.md` — Docker Compose path plus local development |
| API documentation | FastAPI generated OpenAPI at `/docs` |
| Decision log (1–2 pages) | `docs/decision-log.md`, structured against the four required bullets: ambiguities and resolutions (§2), architectural trade-offs (§3, §5), what was not built and why (§9), what would be done with more time |
| Live demo readiness | `docker compose up` with the seeded 10k dataset |
