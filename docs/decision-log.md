# Decision Log — SleekFlow TODO

The four questions the brief asked, answered directly. Reasoning, measurements and
the failures behind these decisions are in
[engineering-notes.md](engineering-notes.md); numbers and query plans are in
[performance.md](performance.md).

## 1. Ambiguities and how they were resolved

- **One shared list, not one list per user.** "Multiple users accessing **the
  same** TODO list" is read literally. There is no `owner_id`. Concurrency is
  therefore a *lost-update* problem answered by versioning, not by identity —
  per-user lists would contradict the wording and make the concurrency
  requirement vacuous, since two people would rarely touch the same row.
- **Archived and deleted are different things.** `archived` is a status the user
  chooses (reversible, still listable); deleting sets `deleted_at` and hides the
  row from every normal query. Modelling them as one concept makes "data should
  not be permanently lost" impossible to express.
- **"Custom" recurrence means unit + interval.** Daily/weekly/monthly are
  (day/week/month, 1); custom is any other pair. Full iCal RRULE is a feature in
  its own right and none of the four stated cases needs it.
- **Recurrence counts from the first occurrence, not the last.** Occurrence *n*
  is `anchor + n × interval`, clamped fresh. Chaining from the previous due date
  drifts off month ends — 31 Jan → 28 Feb → 28 Mar, when the series should reach
  31 Mar.
- **The dependency guard covers `→ completed`, not just `→ in_progress`.** The
  brief blocks only the move to In Progress; left as written a caller could jump
  a blocked task straight to Completed and bypass the feature entirely.
- **Deleting a blocker unblocks its dependents.** Refusing the delete would trap
  tasks behind something nobody can see. The edges survive, so restoring the
  blocker re-establishes the block.

## 2. Architectural decisions and trade-offs

- **Optimistic concurrency, not locking.** Every mutation carries its version in
  `If-Match` and runs as `UPDATE … WHERE id = ? AND version = ?`; zero rows
  updated means someone else won, and the API returns `409` with the current
  state so the client can recover. Locks would serialise writers and be held
  across request round-trips; collisions here are rare, so paying only on
  conflict is cheaper. The same compare-and-set makes recurrence idempotent —
  two concurrent completions cannot both spawn an occurrence.
- **One invariant is enforced in SQL rather than by a test.** `unmet_dependency_count = 0`
  is folded into that same `WHERE`, so "still blocked" and "someone else moved
  first" are decided atomically. There is deliberately no interleaving test: it
  would be flaky by construction, and the predicate is part of the statement that
  commits the row.
- **A denormalised `unmet_dependency_count`.** Computed naively, the
  blocked/unblocked filter is a correlated subquery per row — the hidden trap at
  10k items. Maintained transactionally, it becomes one indexed predicate:
  **8.4 ms** over 10,008 rows.
- **Keyset pagination, not OFFSET.** A deep page seeks into the index instead of
  discarding every row before it. Page 1 and page 101 both run in ~1.2 ms, the
  deep cursor reached by walking 100 real pages. Getting there required matching
  the index to the `coalesce()` sort key and rendering its sentinel as a literal
  rather than a bound parameter — two defects found by reading query plans, both
  now guarded by tests that assert on the *plan* rather than on timings.
- **Cycle detection walks nodes, not paths.** The obvious recursive CTE
  enumerates every path through the graph and is exponential in width — 42 nodes
  took 2 seconds, on the hot path of every dependency add. A `UNION` reachability
  walk visits each node once: 240 nodes answer in 11 ms.
- **A dedicated status endpoint over PATCH.** A transition validates the
  dependency rule, may spawn an occurrence, and recomputes dependent counts. It
  is not a field write, and `POST /todos/{id}/status` returning
  `{todo, next_occurrence}` says so.
- **List responses omit `depends_on`.** Populating edges per item across a
  10,000-row page is an N+1; the list uses the maintained `is_blocked` and
  `unmet_dependency_count` columns instead. The empty array is by design.
- **Authentication gates access and names the actor — it does not scope data.**
  Username and password, bcrypt, 12-hour JWT. The gate sits on the router
  (`dependencies=[Depends(current_user)]`) so a route added later cannot be left
  open by accident, and is declared as an OpenAPI security scheme so the docs
  show it. Attribution (`created_by_id` / `updated_by_id`) is what makes an
  account worth having on a board nobody owns: the conflict banner names a
  person. Dependency edges are attributed on the **edge**, not the todo, because
  adding a link changes only `unmet_dependency_count` — which deliberately does
  not bump `version`.
- **Live updates are server-sent events carrying signals, not state.** Traffic is
  one-directional, so WebSockets buy nothing and cost a protocol upgrade. Each
  frame names what happened and who did it; the client re-reads through the
  normal endpoints rather than patching its cache, so the stream never becomes a
  second write path around the compare-and-set. Fan-out is in-process — correct
  on one worker, and honest about needing a shared bus beyond that.
- **Bulk operations report per item and are not atomic.** On a board where
  *blocked* is a normal state, rolling 15 todos back because 3 were blocked
  destroys useful work. Each item carries its own version and runs in its own
  transaction, so a refusal fails alone; the cost is a round trip per item
  (~2.6 ms), which is what buys the per-item answer.
- **The access token lives in `localStorage`** — a known trade, not an oversight.
  An `httpOnly` cookie prevents *exfiltration* but not in-page abuse, and takes
  on CSRF, which a bearer header is immune to by construction.
- **Vite over Next.js**, and **a monorepo for the assessment**. Next.js's server
  tier would be redundant against FastAPI and add a second runtime to keep alive
  during the demo. One repo means the reviewer clones once; in production these
  would be separate repos coupled only by the OpenAPI contract.

## 3. What was not built and why

- **Per-user ownership** — contradicts "the same TODO list", and would kill the
  concurrency story: two users would almost never touch the same row, so the 409
  path this project is built around would never fire.
- **Refresh tokens, password reset, email verification, roles** — account
  plumbing that demonstrates nothing the brief asks about.
- **iCal RRULE** — unit + interval covers all four stated cases.
- **Cascading un-complete** — reopening a dependency silently re-blocking its
  dependents is surprising, and was not asked for.
- **A frontend test suite** — the UI is thin over a typed API client; the
  complexity worth testing is in backend domain logic, where the 182 tests are.
  The UI is verified by the live demo.
- **Rate limiting and a tuned connection pool** — both real gaps. An in-process
  limiter would only cover one worker, and sizing a pool without a load profile
  is guessing. Mitigations that do exist: identical responses and identical
  bcrypt work whether an account exists or not, at ~197 ms per attempt.
- **Tags, subtasks, comments, attachments, full-text search** — not in the brief.

## 4. What would be done differently with more time

- **A shared event bus** (Postgres `LISTEN`/`NOTIFY`) so live updates survive
  more than one worker — it would change only the publish/subscribe seam.
- **An audit table** — soft delete preserves the row, but only an audit trail
  answers "what changed, and when". It would also let dependency *removal* be
  attributed, which today it is not.
- **Rate limiting on `/api/auth/login`**, backed by a shared store.
- **The access token moved to an `httpOnly` cookie**, with the CSRF defence that
  necessarily comes with it.
- **Contract tests generated from the OpenAPI schema**, guarding the frontend
  client against backend drift.
- **Signed pagination cursors** — they are opaque base64url today, so a client
  can forge one.
