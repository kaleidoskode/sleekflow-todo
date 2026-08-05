async def make(client, name):
    return (await client.post("/api/todos", json={"name": name})).json()


async def link(client, todo, depends_on):
    return await client.post(
        f"/api/todos/{todo['id']}/dependencies", json={"depends_on_id": depends_on["id"]}
    )


async def test_adding_a_dependency_blocks_the_dependent(client):
    a, b = await make(client, "A"), await make(client, "B")
    assert (await link(client, a, b)).status_code == 201

    refreshed = (await client.get(f"/api/todos/{a['id']}")).json()
    assert refreshed["unmet_dependency_count"] == 1
    assert refreshed["is_blocked"] is True
    assert refreshed["depends_on"] == [b["id"]]


async def test_recomputing_counts_does_not_bump_version(client):
    """Derived state must not invalidate clients' versions."""
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    assert (await client.get(f"/api/todos/{a['id']}")).json()["version"] == 1


async def test_direct_cycle_is_rejected(client):
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    response = await link(client, b, a)
    assert response.status_code == 422
    assert response.json()["code"] == "DEPENDENCY_CYCLE"


async def test_multi_hop_cycle_is_rejected(client):
    a, b, c = await make(client, "A"), await make(client, "B"), await make(client, "C")
    await link(client, a, b)
    await link(client, b, c)
    response = await link(client, c, a)
    assert response.status_code == 422
    assert response.json()["code"] == "DEPENDENCY_CYCLE"
    assert len(response.json()["cycle_path"]) >= 3


async def test_self_dependency_is_rejected(client):
    a = await make(client, "A")
    response = await link(client, a, a)
    assert response.status_code == 422


async def test_duplicate_dependency_is_idempotent(client):
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    assert (await link(client, a, b)).status_code in (200, 201)
    assert (await client.get(f"/api/todos/{a['id']}")).json()["unmet_dependency_count"] == 1


async def test_removing_a_dependency_unblocks(client):
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    response = await client.delete(f"/api/todos/{a['id']}/dependencies/{b['id']}")
    assert response.status_code == 204
    assert (await client.get(f"/api/todos/{a['id']}")).json()["is_blocked"] is False


async def test_dependency_on_unknown_todo_returns_404(client):
    a = await make(client, "A")
    response = await client.post(
        f"/api/todos/{a['id']}/dependencies",
        json={"depends_on_id": "018f3b2c-0000-7000-8000-0000000000ff"},
    )
    assert response.status_code == 404
