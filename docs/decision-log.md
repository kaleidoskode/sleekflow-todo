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
- **Real-time updates** — SSE chosen over WebSockets (updates are one-directional, no extra
  infrastructure); design sketched, not built.
- **Bulk operations** — endpoint shape sketched (per-item results so one blocked item does not
  fail the batch); not implemented.
- **Cascading un-complete** — reopening a completed dependency silently re-blocking its dependents
  is surprising behaviour that was not requested.
- **A frontend test suite** — the UI is deliberately thin over a typed API client; the complexity
  worth testing lives in backend domain logic, which is where the 118 tests are. The UI is verified
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
- **Move the access token to an `httpOnly` cookie** — with the CSRF defence that necessarily comes
  with it (see §2). Same-site in both environments here, since cookies ignore port, so
  `SameSite=Lax` would cover `localhost:5173 → localhost:8000` and a shared registrable domain in
  production.
- **Pagination cursor signing** — cursors are opaque base64url of `{value, id}`, so an untrusted
  client can forge them; signing (or encrypting) would confine clients to honest pages.
