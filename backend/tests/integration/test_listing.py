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
