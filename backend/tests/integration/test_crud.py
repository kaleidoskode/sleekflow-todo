async def create_todo(client, **overrides):
    payload = {"name": "Write the spec", "priority": "high"} | overrides
    response = await client.post("/api/todos", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_returns_todo_with_version_one(client):
    todo = await create_todo(client)
    assert todo["name"] == "Write the spec"
    assert todo["priority"] == "high"
    assert todo["status"] == "not_started"
    assert todo["version"] == 1
    assert todo["is_blocked"] is False


async def test_create_rejects_empty_name(client):
    response = await client.post("/api/todos", json={"name": ""})
    assert response.status_code == 422


async def test_unknown_todo_returns_problem_details(client):
    """Task 6's handlers, now exercised through a real route."""
    response = await client.get("/api/todos/018f3b2c-0000-7000-8000-0000000000ff")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "NOT_FOUND"


async def test_create_rejects_recurrence_without_due_date(client):
    response = await client.post(
        "/api/todos",
        json={"name": "Standup", "recurrence_unit": "day", "recurrence_interval": 1},
    )
    assert response.status_code == 422


async def test_get_returns_etag(client):
    todo = await create_todo(client)
    response = await client.get(f"/api/todos/{todo['id']}")
    assert response.status_code == 200
    assert response.headers["etag"] == '"1"'


async def test_patch_increments_version(client):
    todo = await create_todo(client)
    response = await client.patch(
        f"/api/todos/{todo['id']}",
        json={"name": "Renamed"},
        headers={"If-Match": '"1"'},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["version"] == 2


async def test_patch_without_if_match_returns_428(client):
    todo = await create_todo(client)
    response = await client.patch(f"/api/todos/{todo['id']}", json={"name": "Renamed"})
    assert response.status_code == 428
    assert response.json()["code"] == "PRECONDITION_REQUIRED"


async def test_delete_is_soft_and_hides_the_todo(client):
    todo = await create_todo(client)
    assert (await client.delete(f"/api/todos/{todo['id']}", headers={"If-Match": '"1"'})).status_code == 204
    assert (await client.get(f"/api/todos/{todo['id']}")).status_code == 404
    # ...but the row survives and is reachable explicitly.
    found = await client.get(f"/api/todos/{todo['id']}", params={"include_deleted": True})
    assert found.status_code == 200
    assert found.json()["deleted_at"] is not None


async def test_restore_brings_a_deleted_todo_back(client):
    todo = await create_todo(client)
    await client.delete(f"/api/todos/{todo['id']}", headers={"If-Match": '"1"'})
    restored = await client.post(f"/api/todos/{todo['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None
    assert (await client.get(f"/api/todos/{todo['id']}")).status_code == 200
