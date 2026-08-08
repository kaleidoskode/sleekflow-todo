# Performance verification: 10,000+ item TODO list

Verifies the non-functional requirement "the system should handle a TODO list
with 10,000+ items without degrading user experience" with a measured dataset
rather than an assumption.

## Headline result

Measured against a second, independent environment (native PostgreSQL 18,
10,008 rows) with 10 samples per query after warm-up:

| Query | Median | Fastest |
|---|---|---|
| `GET /api/todos?limit=50&sort=name` (page 1) | **5.7 ms** | 5.4 ms |
| Same, page 101 via walked cursor | **5.9 ms** | 5.5 ms |
| `GET /api/todos?limit=50&blocked=true` | 8.4 ms | — |
| `GET /api/todos?limit=50&sort=-priority&status=in_progress` | 6.7 ms | — |

**Page 101 is 0.2 ms slower than page 1** — within noise. That is the entire
claim: with keyset pagination the cost of a page does not depend on how deep
it is. `OFFSET 5000` would read and discard 5,000 rows on every request, and
the cost would climb linearly with depth.

The page-101 cursor was obtained by walking `next_cursor` forward 100 real
pages, not by synthesizing a cursor value — otherwise the test would not be
exercising the deep-page path at all.

One caveat worth stating: the **first** request after the server starts
measures ~310 ms. That is connection-pool warm-up, not query cost. Every
figure above is post-warm-up.

## The default sort was not index-served, and the numbers above did not show it

Worth reading as a finding rather than a footnote: every measurement above
sorts by `name`, which was index-served throughout. **`due_date` — the default
sort, and the one the UI actually issues — was not**, and no test or number
here would have revealed it.

`due_date` is nullable. A row-value comparison against NULL yields NULL, not
true, so the keyset predicate would silently drop every undated todo from the
page after a cursor. The sort key is therefore `coalesce(due_date, sentinel)`.
That was correct — but the index was on the raw `due_date` column, and
PostgreSQL matches an expression index by comparing expression trees. A bare
column is not that expression, so the planner ignored the index entirely:

```
Limit  (cost=777.90..778.03 rows=51)
  ->  Sort  (cost=777.90..802.92 rows=10006)
        Sort Key: (COALESCE(due_date, '9999-12-31'::timestamptz)), id
        Sort Method: top-N heapsort  Memory: 42kB
        ->  Seq Scan on todos  (rows=10007)  Buffers: shared hit=344
Execution Time: 5.322 ms
```

The keyset claim still held — page 101 cost the same as page 1 — but for the
wrong reason: **every page paid a full scan and sort of the table**. That is
O(1) in page depth and O(n) in table size, which is precisely the wrong half of
the requirement to satisfy.

### The second defect, which only appeared under measurement

Adding matching expression indexes (migration `0005`) fixed the plan — and the
ascending sort dropped to 0.9 ms while the **descending sort stayed at 4.8 ms
on the same index**. The sentinel was being sent as a bound parameter. An
expression index is built on a *constant*, so when PostgreSQL builds a generic
plan for a prepared statement the parameter is unknown and the index cannot be
matched.

The failure was intermittent by nature: whether a prepared statement uses a
custom plan (parameter known, index matched) or a generic one is a planner
heuristic. Same code, same indexes, five times slower in one direction:

| Sentinel | `due_date` | `-due_date` |
| --- | ---: | ---: |
| Bound parameter | 0.94 ms | **4.79 ms** |
| Rendered literal | 0.93 ms | 0.93 ms |

Inlining it is safe — the sentinels are module constants, never user input. The
cursor anchor beside them stays bound, because that *is* user input.

### After both fixes

Twenty samples per figure, post-warm-up, 10,010 live rows. Deep pages reached
by walking `next_cursor` forward 100 real pages:

| Sort | Page 1 | Page 101 |
| --- | ---: | ---: |
| `due_date` (default) | **1.13 ms** | 1.27 ms |
| `-due_date` | 1.20 ms | 1.46 ms |
| `priority` | 1.13 ms | 1.17 ms |
| `status` | 1.04 ms | 1.25 ms |
| `name` | 1.09 ms | 1.20 ms |

The default sort went from **5.7 ms to 1.13 ms**, and — the part that matters
more than the ratio — it no longer degrades as the table grows.

### Swept, and one accepted exception

All five sorts were then crossed with every filter (blocked true/false, status,
priority, due window, include-deleted) — 35 plans. Every combination is
index-served **except `include_deleted=true`**, which plans as a sequential scan
at 3.6–6.5 ms.

That is expected rather than broken: every listing index is **partial**, defined
`WHERE deleted_at IS NULL`, because the default listing always excludes deleted
rows and there is no reason for the index to carry them. Dropping that predicate
leaves nothing applicable.

Deliberately not fixed. Making it index-served needs a non-partial duplicate of
all five indexes — doubling write amplification on every insert and update — to
speed up a trash view that is opened rarely and measured at 6.5 ms over 10,007
rows. If it ever mattered, the better fix is not more indexes but a narrower
question: "show me deleted todos" is far more selective than "show me everything
including deleted", and a partial index on `WHERE deleted_at IS NOT NULL` would
be tiny, since deleted rows are a small fraction of the table.

### Cycle detection was exponential in graph width

Found in the same sweep, and worse than the sort defect because it was
unbounded rather than merely slow. `find_cycle_path` runs on **every dependency
add**. It walked the graph with a recursive CTE that carried the path array down
each branch under `UNION ALL` — which enumerates every distinct *path*, not
every node:

| Layers (3 wide) | Nodes | Edges | Paths | Time |
| ---: | ---: | ---: | ---: | ---: |
| 6 | 18 | 45 | 81 | 1.8 ms |
| 9 | 27 | 72 | 2,187 | 3.9 ms |
| 12 | 36 | 99 | 59,049 | **166 ms** |
| 14 | 42 | 117 | 531,441 | **2,018 ms** |

Roughly 3× per added layer. At about fifty nodes it stops answering — and a
project with fifty tasks in a layered dependency graph is not an attack, it is
Tuesday.

The decision is now a node-reachability walk using `UNION` rather than
`UNION ALL`, so the working set is deduplicated by node and each node is
expanded once: O(V+E). The readable path for the error body is built by a
breadth-first walk over the reachable subgraph, and only when a cycle is already
known to exist — so the common "no cycle" answer costs one cheap query:

| Layers × width | Nodes | Edges | Paths | Time |
| --- | ---: | ---: | ---: | ---: |
| 12 × 3 | 36 | 99 | 5.9e4 | 2.7 ms |
| 20 × 4 | 80 | 304 | 6.9e10 | 2.6 ms |
| 30 × 5 | 150 | 725 | 3.7e19 | 5.1 ms |
| 40 × 6 | 240 | 1,404 | 3.7e29 | **11.3 ms** |

Time now tracks nodes and edges, and is flat against a path count spanning
twenty-five orders of magnitude. As a bonus the reported cycle is now the
shortest one rather than whichever path the walk stumbled into first.

`tests/integration/test_query_plans.py` guards both sort defects. It asserts on the
*plan*, not on timings, because timings vary with the machine and the plan does
not: either the sort is index-served or the database is sorting the table. A
separate test asserts the sentinel is inlined rather than bound, because the
plan tests compile with `literal_binds` and structurally cannot catch that one.

## Environments

Measured twice, on two different PostgreSQL installations, to check the result
was a property of the design rather than of one machine.

**Run A — Docker PostgreSQL 16.14** (`postgres:16-alpine`), host port 5433.
Timings via `curl`, which on Windows/Git Bash is dominated by process-spawn
overhead; cross-checked with `urllib`.

**Run B — native PostgreSQL 18**, port 5432. Timings via an in-process
`httpx.AsyncClient`, 10 samples per query after a warm-up request, median
reported. This is the cleaner measurement and is the source of the headline
table above.

Both: API served by `uvicorn app.main:app` on `127.0.0.1:8000`, no reverse
proxy, database on the same machine (loopback only). All timings are
client-observed wall time — HTTP and JSON serialization included, not raw SQL
execution time.

## Dataset

Generated by `backend/seed.py`, run as `python -m seed --count 10000`:

```
Seeded 10000 todos and 5939 dependency edges.
```

- Total rows in `todos` after seeding: 10,001 (1 pre-existing + 10,000 seeded), all live (`deleted_at IS NULL`).
- Dependency edges: 5,939 in `todo_dependencies` (the brief's "~4500" was an approximate
  expectation from the sampling; the actual figure for `random.Random(42)` with
  `dependency_ratio=0.3` and 1–3 targets per dependent item is 5,939 — deterministic
  and reproducible on rerun).
- Rows with `unmet_dependency_count > 0` (the "blocked" set): 2,654 (~26.5% of the table).
- Verified: recomputing `unmet_dependency_count` from `todo_dependencies` for every
  row and diffing against the stored column gives 0 mismatches.
- Verified: a recursive walk of `todo_dependencies` finds 0 cycles (edges are
  constructed strictly backwards in creation order, so the graph is acyclic by
  construction, and this confirms it holds in the actual seeded data).
- `todos` table size: 2752 kB (heap) / 7112 kB (heap + all indexes).

### Reproducibility

The RNG is seeded (`random.Random(42)`), so *structure* is byte-for-byte
identical across reruns: row count, names, statuses, priorities, and the
dependency edge set. Absolute timestamps and UUIDv7 values are **not**
reproducible: `due_date` and `recurrence_anchor_due` are computed as
`datetime.now(UTC) + timedelta(days=...)`, anchored to wall-clock time at
seed time, and `uuid7()` embeds the current timestamp. This is deliberate —
a fixed reference date would make every seeded todo look months overdue by
the time it's viewed in a demo, which is worse for the interview scenario
than losing timestamp-level reproducibility. The counts and query-plan shapes
in this document are reproducible on rerun; the exact due dates are not.

## Run A measurements (Docker PostgreSQL 16)

Each query run 5 times after a warm-up request; values below are the range
observed (all runs were within noise of each other, no outliers beyond one
~110ms cold-cache first hit).

| # | Query | `real` time (5 runs) |
|---|-------|----------------------|
| 1 | `GET /api/todos?limit=50&sort=due_date` | 0.060s – 0.077s (cold first hit 0.112s) |
| 2 | `GET /api/todos?limit=50&sort=-priority&status=in_progress` | 0.056s – 0.066s |
| 3 | `GET /api/todos?limit=50&blocked=true` | 0.057s – 0.080s |
| 4a | `GET /api/todos?limit=50&sort=name` (page 1) | 0.057s – 0.067s |
| 4b | `GET /api/todos?limit=50&sort=name&cursor=<page-100 cursor>` (deep page) | 0.056s – 0.059s |

These are end-to-end `curl` timings and are dominated by `curl`'s own
process-spawn overhead on Windows/Git Bash, not server or query cost — see
the `urllib` cross-check below, which measures actual server-side latency at
~6ms per request, roughly 10x lower than the curl figures suggest.

Method for row 4: walked the `next_cursor` returned by the API forward 99 times
(`limit=50`, `sort=name`) to reach the start of page 100, then timed page 1 and
page 100 back-to-back, 5 curl invocations each. A second, independent
measurement using raw `urllib` requests (no `curl` process-spawn overhead, hitting
`127.0.0.1` to skip the loopback IPv6 resolution delay) gave page 1 = 0.0065s avg
and page 100 = 0.0063s avg over 5 requests each — consistent with the curl figures:
**page 100 is not measurably slower than page 1.**

All four listings return in well under 100ms against a 10,000+ row table — no
degradation observed for any of the filter/sort/pagination combinations the
brief called out.

## Run B measurements (native PostgreSQL 18)

A second run on a different PostgreSQL major version and a separate install,
measured in-process with `httpx` rather than through `curl`, so the numbers
are server latency without process-spawn noise. 10 samples per query after a
warm-up request.

Dataset: 10,008 todos, 2,654 blocked, 5,940 dependency edges. (Slightly above
Run A's counts because a handful of todos had already been created by hand
through the UI on this database before seeding.)

| Query | Median | Fastest |
|---|---|---|
| `?limit=50&sort=name` (page 1) | 5.7 ms | 5.4 ms |
| `?limit=50&sort=name&cursor=<page 101>` | 5.9 ms | 5.5 ms |
| `?limit=50&blocked=true` | 8.4 ms | — |
| `?limit=50&sort=-priority&status=in_progress` | 6.7 ms | — |

Cold start: the first request after `uvicorn` boots measures ~310 ms while the
asyncpg pool opens its first connection. It is not query cost, and it does not
recur. Worth warming the app once before any live demo.

Query plan for the blocked filter on this run:

```
Limit  (cost=0.29..40.71 rows=51 width=147) (actual time=0.042..0.272 rows=51 loops=1)
  Buffers: shared hit=172
  ->  Index Scan using ix_todos_live_name on todos
        (cost=0.29..2103.33 rows=2653 width=147) (actual time=0.040..0.263 rows=51 loops=1)
        Filter: (unmet_dependency_count > 0)
        Rows Removed by Filter: 203
        Buffers: shared hit=172
Planning Time: 0.536 ms
Execution Time: 0.303 ms
```

**Index Scan, not Seq Scan** — same verdict as Run A, on a different major
version. Note the planner again prefers `ix_todos_live_name` (which satisfies
the `ORDER BY`) over `ix_todos_live_blocked`, filtering 203 rows to find 51.
That is the cheaper plan when a sort is present; see the Run A analysis below
for the unsorted case where `ix_todos_live_blocked` does get chosen.

## Index verification (Step 4)

```sql
EXPLAIN ANALYZE
SELECT * FROM todos
WHERE deleted_at IS NULL AND unmet_dependency_count > 0
ORDER BY name, id LIMIT 51;
```

```
 Limit  (cost=0.29..40.54 rows=51 width=147) (actual time=0.064..0.269 rows=51 loops=1)
   ->  Index Scan using ix_todos_live_name on todos  (cost=0.29..2095.27 rows=2654 width=147) (actual time=0.063..0.266 rows=51 loops=1)
         Filter: (unmet_dependency_count > 0)
         Rows Removed by Filter: 197
 Planning Time: 1.651 ms
 Execution Time: 0.685 ms
```

**Verdict: Index Scan, not Seq Scan — the pass/fail bar from the brief is met.**

**Finding worth flagging:** the planner does *not* use `ix_todos_live_blocked`
for this query. It uses `ix_todos_live_name` instead, walking the name-ordered
index and filtering `unmet_dependency_count > 0` inline, because that avoids an
explicit sort step and can stop as soon as 51 matching rows are found. This is
the objectively cheaper plan for `ORDER BY name, id LIMIT 51`, not a bug — but
it means `ix_todos_live_blocked` is not "the index that answers the blocked
filter" in general; it's the index for queries that filter on
`unmet_dependency_count` *without* a more selective sort-order index available.
Confirmed `ix_todos_live_blocked` is real and does get chosen when there's no
competing sort, e.g.:

```sql
EXPLAIN ANALYZE
SELECT count(*) FROM todos WHERE deleted_at IS NULL AND unmet_dependency_count > 0;
```

```
 Aggregate (actual time=0.169..0.170 rows=1 loops=1)
   ->  Index Only Scan using ix_todos_live_blocked on todos  (cost=0.29..70.73 rows=2654 width=0) (actual time=0.033..0.167 rows=2654 loops=1)
         Index Cond: (unmet_dependency_count > 0)
         Heap Fetches: 0
```

The same "sort index wins over filter index" pattern holds for the actual
`blocked=true` API listing, which sorts by the default `due_date` field:

```sql
EXPLAIN ANALYZE
SELECT * FROM todos
WHERE deleted_at IS NULL AND unmet_dependency_count > 0
ORDER BY due_date, id LIMIT 51;
```

```
 Limit  (cost=0.29..37.31 rows=51 width=147) (actual time=0.084..0.276 rows=51 loops=1)
   ->  Index Scan using ix_todos_live_due on todos  (cost=0.29..1927.26 rows=2654 width=147) (actual time=0.083..0.271 rows=51 loops=1)
         Filter: (unmet_dependency_count > 0)
         Rows Removed by Filter: 181
```

Net conclusion: no Seq Scan appears in any variant tested; the partial-index
predicate (`WHERE deleted_at IS NULL`) does match the query predicate, so the
index set is correctly designed — the planner is simply free to (and does)
choose whichever partial index best serves the requested sort order.

## Why keyset pagination keeps page 100 as fast as page 1

Keyset pagination encodes "everything after the last row I saw" as an index
condition — `WHERE (name, id) > (last_name, last_id) ORDER BY name, id LIMIT 51`
— so Postgres seeks directly to that point in the B-tree and reads forward,
doing the same constant amount of work regardless of how many pages precede
it; an `OFFSET`-based scheme would instead have to walk and discard every prior
row, making each deeper page linearly more expensive. This is borne out
directly: `EXPLAIN ANALYZE` on the page-1 query (no cursor) and the page-100
query (with the cursor condition) both plan as an `Index Scan using
ix_todos_live_name` with `Limit`, and both execute in well under a
millisecond — the deep page carries an extra `Index Cond` comparison but no
extra scanned/discarded rows:

Page 1 (no cursor):

```sql
EXPLAIN ANALYZE
SELECT * FROM todos
WHERE deleted_at IS NULL
ORDER BY name, id LIMIT 51;
```

```
 Limit  (cost=0.29..10.84 rows=51 width=147) (actual time=0.037..0.199 rows=51 loops=1)
   ->  Index Scan using ix_todos_live_name on todos  (cost=0.29..2070.27 rows=10001 width=147) (actual time=0.035..0.194 rows=51 loops=1)
 Planning Time: 1.719 ms
 Execution Time: 0.277 ms
```

Page 100 (cursor condition for the row after page 99):

```sql
EXPLAIN ANALYZE
SELECT * FROM todos
WHERE deleted_at IS NULL AND (name, id) > ('Investigate webhooks #318', '019fd00e-e3bf-72e3-8f3c-bc6d7a616218')
ORDER BY name, id LIMIT 51;
```

```
 Limit  (cost=0.29..17.99 rows=51 width=147) (actual time=0.024..0.196 rows=51 loops=1)
   ->  Index Scan using ix_todos_live_name on todos  (cost=0.29..1735.75 rows=5000 width=147) (actual time=0.023..0.191 rows=51 loops=1)
         Index Cond: (ROW((name)::text, id) > ROW('Investigate webhooks #318'::text, '019fd00e-e3bf-72e3-8f3c-bc6d7a616218'::uuid))
 Planning Time: 1.601 ms
 Execution Time: 0.286 ms
```

(0.277ms vs 0.286ms — re-taken during the fix-round review to attach the
underlying plans to the doc; these numbers differ slightly from the 0.255ms
/ 0.158ms originally quoted, as expected from normal `EXPLAIN ANALYZE`
run-to-run variance on sub-millisecond queries. The conclusion is unchanged:
the deep page is not slower.)

## Bulk operations: cost of per-item isolation

Batch endpoints run each item in **its own transaction**, so a blocked or stale
item fails alone (see the decision log). That isolation is bought with a round
trip per item rather than one statement for the batch, so it is worth knowing
what it costs.

Measured through the API on native PostgreSQL 18, median of 5 runs per size,
each run cycling the whole selection through a real status transition:

| Items | Median | Per item | Min–max |
| ----: | -----: | -------: | ------: |
|    10 |  28.7 ms |  2.87 ms |  27–138 ms |
|    50 | 128.3 ms |  2.57 ms | 120–133 ms |
|   200 | 524.6 ms |  2.62 ms | 475–548 ms |

**Linear, at roughly 2.6 ms per item.** The full 200-item maximum completes in
about half a second, which is inside what a click can absorb without feeling
broken — and 200 is the ceiling precisely because it is the largest page the
list endpoint will hand out, so "select everything on screen" always fits in
one request.

The first sample of each size is consistently the slowest (the 138 ms outlier
at 10 items is a cold connection pool), which is why medians are reported. A
second run of the same script landed within noise of these figures
(31.8 / 125.8 / 532.9 ms), so the per-item constant is stable.

Two things this deliberately does **not** do:

- **It does not run items concurrently.** Each item holds a pooled connection
  for its transaction, so fanning 200 out with `gather()` would ask for 200
  connections against a pool of five and deadlock. Bounded concurrency — a
  semaphore sized to the pool — is the optimisation if this ever became hot.
- **It does not collapse into one `UPDATE ... WHERE id = ANY(...)`.** That
  would be a single fast statement, and it would make partial success
  impossible to report: a set-based update cannot say *which* rows it skipped
  or *why*. The per-item cost is what buys the per-item answer.

Reproduce with `cd backend && uv run python bench_bulk.py` — it runs against
`todo_test` and refuses to start anywhere else, since it drops the schema first.
