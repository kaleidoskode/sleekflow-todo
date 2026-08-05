import asyncio


async def test_stale_version_is_rejected_with_409(client):
    todo = (await client.post("/api/todos", json={"name": "Shared task"})).json()
    first = await client.patch(
        f"/api/todos/{todo['id']}", json={"name": "User A"}, headers={"If-Match": '"1"'}
    )
    assert first.status_code == 200

    second = await client.patch(
        f"/api/todos/{todo['id']}", json={"name": "User B"}, headers={"If-Match": '"1"'}
    )
    assert second.status_code == 409
    body = second.json()
    assert body["code"] == "VERSION_CONFLICT"
    # The conflict body must carry current state so the UI can show what changed.
    assert body["current"]["name"] == "User A"
    assert body["current"]["version"] == 2


async def test_concurrent_updates_produce_exactly_one_winner(client):
    todo = (await client.post("/api/todos", json={"name": "Contended"})).json()
    responses = await asyncio.gather(
        *[
            client.patch(
                f"/api/todos/{todo['id']}",
                json={"name": f"writer-{i}"},
                headers={"If-Match": '"1"'},
            )
            for i in range(5)
        ]
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(200) == 1
    assert codes.count(409) == 4
