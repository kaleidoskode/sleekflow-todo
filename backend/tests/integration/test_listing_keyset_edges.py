"""Edge cases the task-8 brief's test set does not exercise directly, but the
task's own review guidance calls out as fiddly and worth verifying against a
real database rather than assuming: row-value comparison of a cursor value
against a native enum column, and NULL due_dates in the keyset predicate.
"""

from datetime import UTC, datetime, timedelta

NOW = datetime(2026, 6, 1, tzinfo=UTC)


async def test_cursor_walks_status_sort_without_gaps_or_repeats(client):
    """Sorting/paging by status exercises the row-value comparison against the
    native `todo_status` enum. Without an explicit cast, PostgreSQL rejects the
    comparison with `operator does not exist: todo_status > character varying`
    because the cursor's decoded value arrives as a plain Python str.
    """
    names = []
    for i in range(7):
        response = await client.post(
            "/api/todos", json={"name": f"status-task-{i:02d}", "priority": "medium"}
        )
        names.append(response.json()["name"])

    seen, cursor = [], None
    while True:
        params = {"limit": 2, "sort": "status"}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get("/api/todos", params=params)).json()
        seen.extend(t["name"] for t in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == set(names)


async def test_cursor_pages_through_null_and_non_null_due_dates(client):
    """due_date is nullable; the sort expression COALESCEs to a sentinel so the
    row-value comparison never hits NULL. Ascending sort must put nulls last.
    """
    with_due = []
    for i in range(4):
        response = await client.post(
            "/api/todos",
            json={"name": f"due-task-{i:02d}", "due_date": (NOW + timedelta(days=i)).isoformat()},
        )
        with_due.append(response.json()["name"])

    without_due = []
    for i in range(3):
        response = await client.post("/api/todos", json={"name": f"nodue-task-{i:02d}"})
        without_due.append(response.json()["name"])

    seen, cursor = [], None
    while True:
        params = {"limit": 2, "sort": "due_date"}
        if cursor:
            params["cursor"] = cursor
        body = (await client.get("/api/todos", params=params)).json()
        seen.extend(t["name"] for t in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == set(with_due) | set(without_due)
    # Ascending + nulls-last: every due-dated todo must appear before every
    # todo with no due_date at all.
    last_due_index = max(seen.index(n) for n in with_due)
    first_null_index = min(seen.index(n) for n in without_due)
    assert last_due_index < first_null_index
