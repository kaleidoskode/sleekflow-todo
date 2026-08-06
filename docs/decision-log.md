# Decision Log — SleekFlow TODO

The assignment asked for four things from this log: how ambiguous requirements were interpreted,
the key architectural trade-offs, what was deliberately not built, and what would be done with more
time. The four sections below answer those directly.

## 1. Ambiguities and how they were resolved

- **One shared list, no per-user ownership.** "Multiple users accessing **the same** TODO list"
  is read literally: one list, no accounts. Concurrency is therefore a *lost-update* problem and
  is answered by versioning (§2), not by identity. Per-user lists were rejected — they contradict
  the wording and would make the concurrency requirement nearly vacuous, since two users would
  rarely touch the same row.
- **Archived vs. deleted are two different things.** `Archived` is a lifecycle *status* the user
  chooses (reversible, still listable); `deleted` means a `deleted_at` soft delete — hidden from
  all normal queries, restorable, visible only through `include_deleted`. Modelling them as one
  concept would have made "no permanent data loss" impossible to express.
- **"Custom" recurrence is unit + interval.** Daily / weekly / monthly map to
  (day/week/month, 1); custom is any other pair — every 3 days, every 2 weeks. Full iCal RRULE was
  cut: it is a multi-day feature on its own and none of the four stated cases needs it.
- **Recurrence anchors to the first occurrence.** Occurrence *n* is due at `anchor + n × interval`,
  clamped fresh each time — not `previous due + interval`. Chaining drifts off month ends
  (31 Jan → 28 Feb → 28 Mar, when the series should hit 31 Mar). A late completion advances the
  index until the due date lands in the future, so no backlog of missed occurrences spawns.
- **The `→ completed` bypass was closed.** The assignment blocks only the move to `In Progress`.
  Left as written, a caller could jump a blocked task straight to `Completed` and bypass the
  feature entirely, so the dependency guard applies to both transitions.
- **Deleting a blocker unblocks its dependents.** Refusing deletion would trap dependents behind a
  task nobody can see. The dependency rows are preserved, so restoring the deleted task
  re-establishes the block.

## 2. Architectural decisions and trade-offs

- **Optimistic concurrency over pessimistic locking.** Every mutation carries the row version in
  an `If-Match` header and executes as `UPDATE ... WHERE id = ? AND version = ?`; zero rows
  updated means another writer won, and the API answers `409` with the current state so the client
  can recover. Row locks would serialize writers and hold locks across request round-trips; the
  collision rate on a shared list is low, so paying only on conflict is cheaper than paying on
  every write. The same CAS makes recurrence idempotent: two concurrent "complete this recurring
  task" calls cannot both spawn an occurrence — one mechanism, two problems.
- **A denormalised `unmet_dependency_count`.** The blocked/unblocked filter is the hidden
  performance trap at 10k rows: computed naively it is a correlated subquery per row. Maintaining
  the count transactionally whenever an edge changes or a status moves turns the filter into one
  indexed predicate and the blocking guard into a field read. The write cost is real, but the read
  side wins here — the assignment's hard requirement is the 10,000+ item list, and listing is the
  most frequent operation.
- **Keyset pagination over OFFSET.** "Everything after the last row I saw" becomes an index
  condition, so page 100 seeks directly into the B-tree and costs the same as page 1; OFFSET walks
  and discards every prior row, degrading linearly. Measured: 0.277 ms vs 0.286 ms
  ([docs/performance.md](performance.md)).
- **A dedicated status endpoint over PATCH.** A transition is not a field write — it validates the
  dependency rule, may spawn a recurrence occurrence, and recomputes dependent counts.
  `POST /api/todos/{id}/status` makes those semantics explicit and returns `{todo, next_occurrence}`,
  so completing a recurring task tells the client what it created.
- **Anchor + index recurrence over previous-due chaining.** Two extra columns buy exact month-end
  behaviour (the §1 drift example) and make each occurrence independently addressable, which the
  idempotent-spawn guarantee relies on.
- **Vite over Next.js.** Next.js's value is its server tier, which would be redundant against a
  separate FastAPI backend and would add a second runtime to keep alive during the demo. TanStack
  Query supplies cache invalidation and makes conflict handling demonstrable in the UI.
- **A concurrency invariant is enforced in SQL, not by a test.** The final guard on the
  dependency-protected transitions is `unmet_dependency_count = 0` folded into the CAS's `WHERE`
  clause (`update_versioned` in `backend/app/repositories/todo_repo.py`) — so "still blocked" and
  "someone else moved first" are decided atomically inside the same `UPDATE` that bumps the
  version. There is deliberately **no interleaving test** for this race: proving the moment between
  reading the count and writing would require timing two transactions at a precise point, which is
  flaky by construction. The invariant is verified by reading the `WHERE` clause — the predicate is
  part of the statement that commits the row, so a blocked todo cannot durably complete regardless
  of timing. The concurrent-write tests cover the version half of the CAS (exactly one winner out
  of five simultaneous updates); the dependency half is structural, and that is the honest truth.
- **List responses deliberately omit `depends_on`.** Only `GET /api/todos/{id}` populates it.
  Populating per item across a 10,000-row page would be an N+1 query; the list UI instead uses
  `is_blocked` and `unmet_dependency_count`, which are maintained columns. The empty array in list
  responses is by design, not an oversight.
- **A monorepo for the assessment, separate repos in production.** Frontend and backend share no
  code — no cross-imports, no shared build steps. The only coupling is the API contract, which lives
  in the OpenAPI schema. They live in one repo here so the reviewer clones once and runs one
  `docker compose up`. In production, separate repos with contract tests generated from the OpenAPI
  schema keep deployment lifecycles independent and prevent a frontend change from blocking a
  backend deploy.
- **A bundled Postgres container for the demo, a managed database in production.** The compose file
  provisions a `postgres:16-alpine` container so the reviewer's stack is self-contained — no
  connection string to configure, nothing to install. The application reads `DATABASE_URL` from the
  environment and does not depend on how it got there; pointing it at RDS or Cloud SQL is a
  configuration change, not a code change.

## 3. What was not built and why

- **Authentication** — contradicts "the same TODO list"; concurrency is answered by versioning,
  not identity.
- **iCal RRULE recurrence** — a multi-day feature; unit + interval covers all four stated cases.
- **Real-time updates** — SSE chosen over WebSockets (updates are one-directional, no extra
  infrastructure); design sketched, not built.
- **Bulk operations** — endpoint shape sketched (per-item results so one blocked item does not
  fail the batch); not implemented.
- **Cascading un-complete** — reopening a completed dependency silently re-blocking its dependents
  is surprising behaviour that was not requested.
- **A frontend test suite** — the UI is deliberately thin over a typed API client; the complexity
  worth testing lives in backend domain logic, which is where the 97 tests are. The UI is verified
  by the live demo.
- **Tags, subtasks, comments, attachments, full-text search** — not in the assignment.

## 4. What would be done differently with more time

- **SSE for real-time updates** — implement the sketched design (event stream, clients invalidate
  queries on each event) end to end.
- **An audit table (or outbox) for full history** — soft delete preserves the row, but only a full
  audit trail answers "what changed, and when" for every field.
- **RRULE** — if real users turned out to need complex schedules (every 2nd Tuesday, end dates,
  occurrence counts).
- **Contract tests generated from the OpenAPI schema** — guard the frontend client against backend
  drift without hand-maintained stubs.
- **Pagination cursor signing** — cursors are opaque base64url of `{value, id}`, so an untrusted
  client can forge them; signing (or encrypting) would confine clients to honest pages.
