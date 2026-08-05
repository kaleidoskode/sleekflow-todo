import asyncio
from datetime import UTC, datetime

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
    await client.post(f"/api/todos/{b['id']}/restore", headers={"If-Match": '"2"'})
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
    assert spawned["recurrence_series_id"] is not None
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


async def test_stale_if_match_on_status_returns_409_not_422(client):
    """A stale client must get back `current` so it can recover its version."""
    todo = await make(client)
    assert (await set_status(client, todo, "completed")).status_code == 200

    stale = await set_status(client, todo, "completed", version=1)
    assert stale.status_code == 409
    assert stale.json()["code"] == "VERSION_CONFLICT"
    assert stale.json()["current"]["version"] == 2


async def test_status_endpoint_without_if_match_returns_428(client):
    todo = await make(client)
    response = await client.post(
        f"/api/todos/{todo['id']}/status", json={"status": "in_progress"}
    )
    assert response.status_code == 428


async def test_reopening_a_blocker_reblocks_its_dependents(client):
    """Count propagation must not live inside the `if COMPLETED` branch."""
    a, b = await make(client, name="A"), await make(client, name="B")
    await client.post(f"/api/todos/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    await set_status(client, b, "completed")
    assert (await client.get(f"/api/todos/{a['id']}")).json()["is_blocked"] is False

    await set_status(client, b, "not_started", version=2)
    assert (await client.get(f"/api/todos/{a['id']}")).json()["is_blocked"] is True


async def test_patch_adding_recurrence_seeds_a_series_id(client):
    todo = await make(client, due_date=FUTURE.isoformat())
    patched = await client.patch(
        f"/api/todos/{todo['id']}",
        json={"recurrence_unit": "week", "recurrence_interval": 1},
        headers={"If-Match": '"1"'},
    )
    assert patched.status_code == 200
    assert patched.json()["recurrence_series_id"] is not None

    body = (await set_status(client, patched.json(), "completed", version=2)).json()
    spawned = body["next_occurrence"]
    assert spawned["recurrence_series_id"] is not None
    assert spawned["recurrence_series_id"] == patched.json()["recurrence_series_id"]
