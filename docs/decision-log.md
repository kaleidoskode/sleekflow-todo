# Decision Log — SleekFlow TODO

The assignment asked for four things from this log: how ambiguous requirements were interpreted,
the key architectural trade-offs, what was deliberately not built, and what would be done with more
time. The four sections below answer those directly.

## 1. Ambiguities and how they were resolved

- **One shared list, no per-user ownership.** "Multiple users accessing **the same** TODO list"
  is read literally: one board that everyone works on. Accounts were added later (§2), but only as
  a gate and an identity — there is no `owner_id`, and signing in changes nothing about *what* you
  see. Concurrency is therefore a *lost-update* problem answered by versioning, not by identity.
  Per-user lists were rejected: they contradict the wording, and they would make the concurrency
  requirement nearly vacuous, since two users would rarely touch the same row.
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
  most frequent operation. Measured: the blocked filter returns in **8.4 ms** over 10,008 rows
  with 2,654 of them blocked, planning as an Index Scan with 172 buffer hits and 0.303 ms
  execution — no sequential scan.
- **Keyset pagination over OFFSET.** "Everything after the last row I saw" becomes an index
  condition, so a deep page seeks straight into the B-tree and costs the same as the first; OFFSET
  walks and discards every prior row, degrading linearly with depth. Measured end-to-end through
  the API against 10,008 rows: **page 1 at 5.7 ms, page 101 at 5.9 ms** (medians of 10 samples).
  The page-101 cursor was reached by walking `next_cursor` forward 100 real pages rather than
  synthesizing one, so the deep-page path is genuinely exercised. Confirmed on two PostgreSQL
  major versions (16 in Docker, 18 native) to check the result belonged to the design rather than
  one machine ([docs/performance.md](performance.md)).
- **The sort key is `coalesce(due_date, sentinel)`, and both the index and the literal follow from
  that.** `due_date` is nullable, and a row-value comparison against NULL yields NULL rather than
  true — so a plain keyset predicate silently drops every undated todo from the page after a
  cursor. Coalescing to a sentinel (one per direction, so undated todos sort last either way)
  fixes correctness, and it moves two things that are easy to get wrong. First, the **index must be
  built on the same expression**: an index on the raw column cannot serve
  `ORDER BY coalesce(due_date, ...)`, because PostgreSQL matches expression indexes by comparing
  expression trees. That was the original mistake — the default sort planned as a sequential scan
  plus a top-N sort of the whole table, at 5.3 ms against 0.09 ms for the index scan. The keyset
  claim still held (page 101 cost what page 1 did) but for the wrong reason: every page paid for
  the entire table, which is O(1) in depth and O(n) in size. Second, the **sentinel must be
  rendered into the SQL, not bound**: an expression index is built on a constant, so a generic
  plan for a prepared statement cannot match `coalesce(due_date, $1)`. That one failed
  intermittently — custom-versus-generic plan selection is a planner heuristic, and the ascending
  sort kept its index at 0.9 ms while the descending sort silently went generic at 4.8 ms. Both
  are guarded by tests that assert on the **plan** rather than on timings, since timings vary with
  the machine and the plan does not ([docs/performance.md](performance.md)).
- **Cycle detection walks nodes, not paths.** The guard on every dependency add asks "is the
  proposed blocker already downstream of this todo?" The first implementation answered it with a
  recursive CTE that carried the path array down each branch under `UNION ALL` — which enumerates
  every distinct *path* through the graph rather than every node, and is therefore exponential in
  width. Measured on a layered graph three wide: 36 nodes took 166 ms and 42 nodes took 2.0 s,
  tripling per added layer, so around fifty nodes it stopped answering. Fifty tasks in a layered
  dependency graph is an ordinary project, not an attack. The decision is now a reachability walk
  using `UNION`, which deduplicates the working set by node so each is expanded once — O(V+E), and
  it terminates on cyclic data without needing a path guard at all. The readable `cycle_path` in
  the error body is built separately, by a breadth-first walk over the reachable subgraph, and only
  once a cycle is known to exist — so the common "no cycle" answer is one cheap query. 240 nodes
  and 1,404 edges (3.7e29 paths) now answer in 11 ms, and the reported cycle is the shortest one
  rather than whichever the walk found first ([docs/performance.md](performance.md)).
- **The trash view is not index-served, and that is the accepted trade.** Every listing index is
  partial (`WHERE deleted_at IS NULL`), because the default listing always excludes deleted rows.
  `include_deleted=true` therefore drops the predicate the indexes are defined on and plans as a
  sequential scan — measured at 3.6–6.5 ms over 10,007 rows. Making it index-served would mean a
  non-partial duplicate of all five sort indexes, doubling write amplification on every insert and
  update to speed up a view that is opened rarely. The better fix, if it ever mattered, is a
  narrower question rather than more indexes: "show me deleted todos" is highly selective and a
  partial index on `deleted_at IS NOT NULL` would be tiny. Stated here because a sequential scan
  that nobody has noticed is a bug, while one that has been measured and priced is a decision.
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
- **Authentication gates access and names the actor; it does not scope data.** Register and sign in with a username
  and password, bcrypt-hashed, exchanged for a 12-hour JWT sent as a bearer token. The gate is
  applied at the router level (`dependencies=[Depends(current_user)]`) rather than per endpoint, so
  a route added later cannot be left unprotected by accident. The gate is declared as an OpenAPI
  security scheme rather than read off the raw request, so the generated docs show it: an Authorize
  button and a padlock on each of the ten gated operations, with `/health`, `/register` and
  `/login` visibly open. Enforcement that is invisible in the schema still works, but nobody
  reading the API can tell it is there. Two details worth calling out: a
  functional unique index on `lower(username)` backs the case-insensitive lookup, because a plain
  unique constraint would let "Ada" and "ada" both register and make the lookup ambiguous; and a
  wrong password and an unknown account return the identical body after the same bcrypt work, so
  the endpoint cannot be used to enumerate accounts. The app refuses to boot on the committed
  development signing secret outside a local environment — a signing key in the repository means
  anyone who can read the repo can mint valid tokens.
- **The access token is held in `localStorage`, which is a known trade — not an oversight.** An
  `httpOnly` cookie is the stronger option and was consciously deferred. What it actually buys is
  narrower than it is usually credited with: under XSS an attacker can still issue authenticated
  requests, because the browser attaches the cookie for them. What it prevents is *exfiltration* —
  the token cannot be read out and replayed elsewhere for the rest of its 12-hour life. Against
  that, cookies are attached automatically, which is precisely the property CSRF exploits, so the
  migration takes on a class of attack that a bearer header is immune to by construction (a
  cross-site form cannot set `Authorization`). The full move is a cookie on login/logout, a CSRF
  double-submit token, `allow_credentials` on CORS, and a frontend that bootstraps identity from
  `/api/auth/me` instead of storage. Deferred because authentication was outside the original
  scope, and a rushed migration of the auth path buys less than naming the trade honestly. The
  exposure is bounded by the token's 12-hour lifetime and by there being no privileged account —
  every user sees the same board either way.
- **Attribution, but still no ownership.** `todos.created_by_id` / `updated_by_id` record who
  acted, so the conflict banner names a person — "grace changed this" rather than "someone else" —
  which is what makes an account worth having on a board nobody owns. Both are nullable, because
  seeded rows have no actor, and `ON DELETE SET NULL`, so removing an account never destroys a
  todo. Deliberately *not* written by dependency-count recomputes: that is derived state which
  must stay invisible to other clients, so it neither bumps `version` nor claims an author. The
  list endpoint resolves every actor for a page in one query rather than one per row, for the same
  N+1 reason `depends_on` is omitted there.
- **Dependency edges are attributed on the edge, not on the todo.** "Who blocked my task?" was
  unanswerable: every other mutation recorded an actor and this one did not. The fix records
  `created_by_id` / `created_at` on `todo_dependencies` rather than writing `updated_by_id` on the
  dependent, and the reason is the same principle that governs the count column. Adding a link
  changes exactly one thing about the dependent todo — `unmet_dependency_count` — which is
  deliberately maintained *without* bumping `version`. Writing an author onto the todo would
  therefore change who it claims last touched it with no version change for any client to detect,
  leaving two tabs quietly disagreeing. Attributing the edge keeps the claim exactly as precise as
  the fact. **The version is deliberately not bumped either**, for consistency: the other path
  that changes that same column — completing a blocker, which unblocks its dependents — cannot bump
  it, so bumping here would make two routes to the same state behave differently. Safety is
  unaffected, because the `unmet_dependency_count = 0` predicate folded into the CAS already
  catches a stale client that tries to start a newly blocked todo. The cost is honest and worth
  stating: **removal is not attributed** — deleting the edge takes the record with it, and a full
  history is what an audit table is for. `GET /api/todos/{id}` now returns `depends_on` as objects
  (`id`, `added_by`, `added_at`) rather than bare ids; list responses still send `[]`.
- **Server-sent events over WebSockets, carrying signals rather than state.** Live updates are
  one-directional — the server announces, the client never replies — so a bidirectional transport
  buys nothing and costs a protocol upgrade, a second thing to keep alive, and reconnection logic
  of its own. SSE is plain HTTP: a reconnect is just another request. Two consequences shaped the
  implementation. First, `EventSource` **cannot set request headers**, so it cannot carry the
  bearer token; the options were a token in the query string, where it lands in every access log,
  or reading the stream with `fetch` and a `ReadableStream`, which is what the frontend does.
  Second, each frame is a **signal, not a payload**: it names what happened and who did it, and the
  client responds by re-reading through the normal endpoints. Applying event bodies directly would
  let an out-of-order delivery overwrite newer state — reintroducing exactly the lost update the
  versioning scheme exists to prevent — and would make the stream a second write path that
  bypasses the compare-and-set. Events are published from the routers after the service has
  committed, so nothing is announced that a reader could fail to see; a rejected write publishes
  nothing at all. The fan-out is **in-process**, which is honest about its limit: one uvicorn
  worker fans out correctly, two do not. The fix is a shared bus — Postgres `LISTEN`/`NOTIFY`
  needs no new infrastructure since the database is already there — and it would change only the
  publish/subscribe seam, not the routers or the client.
- **Bulk operations report per item, and are not atomic.** All-or-nothing was the alternative and
  it is the wrong shape here: on a board where *blocked* is a normal, expected state rather than
  an error, rolling 15 todos back because 3 of them were blocked destroys useful work and tells
  the user nothing. `POST /api/todos/bulk/status` and `/bulk/delete` answer `{succeeded, failed,
  results[]}` with the code and sentence the single-item endpoint would have given for each row.
  Three consequences worth naming. **Versions move into the body**, one per item, because a batch
  has a single `If-Match` header and many rows — optimistic concurrency survives batching, and a
  stale item is refused rather than forced through. **The status is always `200`**: the batch
  request itself succeeded, and the outcomes are its payload; `207 Multi-Status` was considered
  and rejected as a WebDAV extension whose body format is XML, so reusing the code with a
  different body would invite clients to guess. **Isolation is structural, not argued** — each
  item runs in its own session and therefore its own transaction, because sharing one would let a
  single failed statement poison every item after it. The cost of that choice is a round trip per
  item, measured at ~2.6 ms/item and linear to the 200-item ceiling
  ([docs/performance.md](performance.md)); collapsing it into one set-based `UPDATE ... WHERE id
  = ANY(...)` would be far faster and could not report *which* rows it skipped or *why*, which is
  the entire feature.
- **A batch publishes one event, not one per item.** Every live-update event costs each watching
  tab a refetch, so announcing 200 individual changes would turn one click into 200 rounds of
  invalidation per connected client — the feature paying for itself in reverse. A batch is one
  thing that happened, so it is one event carrying a count. A batch where nothing succeeded
  publishes nothing at all.
- **The event stream authenticates without holding a database session.** A FastAPI dependency
  declared with `yield` stays open until the response finishes, and an SSE response never
  finishes. Using the ordinary `current_user` on the stream would therefore pin one pooled
  connection per connected browser and exhaust the pool at a handful of open tabs — a bug that
  would surface only under exactly the multi-tab conditions this feature exists for. `streaming_user`
  opens its own session, resolves the token, and releases it before the first byte of the body.
- **A bundled Postgres container for the demo, a managed database in production.** The compose file
  provisions a `postgres:16-alpine` container so the reviewer's stack is self-contained — no
  connection string to configure, nothing to install. The application reads `DATABASE_URL` from the
  environment and does not depend on how it got there; pointing it at RDS or Cloud SQL is a
  configuration change, not a code change.

## 3. What was not built and why

- **Per-user todo ownership** — authentication was added (see §2), but *ownership* was not. There
  is no `todos.owner_id`; every signed-in account sees and edits the same board. Scoping todos per
  user would contradict "multiple users accessing **the same** TODO list", and would quietly kill
  the concurrency story — two users would almost never touch the same row, so the 409 path this
  project is built around would never fire.
- **Refresh tokens, password reset, email verification, roles** — a single 12-hour access token is
  enough to demonstrate the gate. Everything else is account-management plumbing that would not
  show anything the assignment asks about.
- **iCal RRULE recurrence** — a multi-day feature; unit + interval covers all four stated cases.
- **Cascading un-complete** — reopening a completed dependency silently re-blocking its dependents
  is surprising behaviour that was not requested.
- **A frontend test suite** — the UI is deliberately thin over a typed API client; the complexity
  worth testing lives in backend domain logic, which is where the 178 tests are. The UI is verified
  by the live demo.
- **Tags, subtasks, comments, attachments, full-text search** — not in the assignment.

## 4. What would be done differently with more time

- **A shared event bus, so live updates survive more than one worker** — Postgres
  `LISTEN`/`NOTIFY` behind the same publish/subscribe seam the in-process broker uses (see §2).
- **An audit table (or outbox) for full history** — soft delete preserves the row, but only a full
  audit trail answers "what changed, and when" for every field.
- **RRULE** — if real users turned out to need complex schedules (every 2nd Tuesday, end dates,
  occurrence counts).
- **Contract tests generated from the OpenAPI schema** — guard the frontend client against backend
  drift without hand-maintained stubs.
- **Move the access token to an `httpOnly` cookie** — with the CSRF defence that necessarily comes
  with it (see §2). Same-site in both environments here, since cookies ignore port, so
  `SameSite=Lax` would cover `localhost:5173 → localhost:8000` and a shared registrable domain in
  production.
- **Rate limiting on `/api/auth/login`** — nothing throttles attempts today. Two mitigations are
  already in place and neither is sufficient: a wrong password and an unknown account return the
  identical body after the same bcrypt work, so the endpoint cannot be used to enumerate accounts;
  and bcrypt at its default cost of 12 measures **197 ms per attempt**, bounding an attacker to
  roughly 5 guesses per second per core. That same cost cuts the other way — 197 ms of CPU per
  unauthenticated request makes login the cheapest way to exhaust the server, which is the stronger
  argument for a limiter than brute force is. Deliberately not built for the assessment: a limiter
  held in process would only cover one worker, the same caveat the event broker carries, so the
  honest version needs a shared store (Redis, or a `LISTEN`/`NOTIFY`-backed counter) and that is
  infrastructure this deliverable does not otherwise need.
- **A connection pool sized from a load profile** — the pool is SQLAlchemy's default of 5 with 10
  overflow. That is an unexamined default rather than a chosen number, and it is called out here
  because the one bug that would have exhausted it — the event stream holding a session for the
  life of a connected tab — was real and is fixed (§2). Sizing it properly means measuring
  concurrent request patterns against database capacity, which a demo has no basis to do; picking
  a bigger number without that measurement is guessing with extra steps.
- **Pagination cursor signing** — cursors are opaque base64url of `{value, id}`, so an untrusted
  client can forge them; signing (or encrypting) would confine clients to honest pages.
