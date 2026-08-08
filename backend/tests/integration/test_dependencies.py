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
    assert [d["id"] for d in refreshed["depends_on"]] == [b["id"]]


async def test_recomputing_counts_does_not_bump_version(client):
    """Derived state must not invalidate clients' versions."""
    a, b = await make(client, "A"), await make(client, "B")
    await link(client, a, b)
    assert (await client.get(f"/api/todos/{a['id']}")).json()["version"] == 1


class TestDependencyAttribution:
    """Who drew the link, recorded on the edge rather than on the todo."""

    async def test_edge_records_who_added_it(self, client):
        a, b = await make(client, "A"), await make(client, "B")
        await link(client, a, b)

        edge = (await client.get(f"/api/todos/{a['id']}")).json()["depends_on"][0]
        assert edge["id"] == b["id"]
        assert edge["added_by"] == "fixture-user"
        assert edge["added_at"] is not None

    async def test_attribution_does_not_touch_the_todo(self, client):
        """The whole reason it lives on the edge.

        `unmet_dependency_count` is maintained without bumping `version`, so
        writing an author onto the todo here would change who it claims last
        touched it with no version change for a client to notice.
        """
        a, b = await make(client, "A"), await make(client, "B")
        before = (await client.get(f"/api/todos/{a['id']}")).json()
        await link(client, a, b)
        after = (await client.get(f"/api/todos/{a['id']}")).json()

        assert after["version"] == before["version"]
        assert after["updated_by"] == before["updated_by"]

    async def test_re_adding_keeps_the_original_author(self, client):
        """The first person to block the todo is the one who blocked it."""
        a, b = await make(client, "A"), await make(client, "B")
        await link(client, a, b)
        first = (await client.get(f"/api/todos/{a['id']}")).json()["depends_on"][0]

        assert (await link(client, a, b)).status_code == 201  # idempotent re-add
        second = (await client.get(f"/api/todos/{a['id']}")).json()["depends_on"][0]
        assert second["added_at"] == first["added_at"]
        assert second["added_by"] == first["added_by"]

    async def test_edges_come_back_oldest_first(self, client):
        a = await make(client, "A")
        blockers = [await make(client, f"blocker {i}") for i in range(3)]
        for blocker in blockers:
            await link(client, a, blocker)

        listed = (await client.get(f"/api/todos/{a['id']}")).json()["depends_on"]
        # Unordered, this reshuffles between reads and the panel jumps around.
        assert [d["id"] for d in listed] == [b["id"] for b in blockers]

    async def test_list_responses_still_omit_edges(self, client):
        """Attribution must not have quietly turned the list into an N+1."""
        a, b = await make(client, "A"), await make(client, "B")
        await link(client, a, b)
        items = (await client.get("/api/todos")).json()["items"]
        assert all(item["depends_on"] == [] for item in items)


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


class TestCycleDetectionScales:
    """Cycle detection must be bounded by the graph, not by paths through it.

    The original probe carried a path array down every branch under UNION ALL,
    which enumerates every distinct *path* rather than every node. Measured, a
    3-wide layered graph of 36 nodes took 166 ms and 42 nodes took 2.0 s,
    roughly tripling per added layer — so a graph of about fifty nodes stopped
    answering. This runs on every dependency add, so an ordinary project graph
    could have made the endpoint unusable.
    """

    @staticmethod
    async def _lattice(session, layers: int, width: int):
        """Layered graph, every node linked to every node in the next layer.

        Node count stays small while the path count explodes — 20 layers of 4 is
        80 nodes but 6.9e10 distinct paths. That gap is the whole point.
        """
        from sqlalchemy import text
        from uuid6 import uuid7

        grid = [[uuid7() for _ in range(width)] for _ in range(layers)]
        await session.execute(
            text(
                "INSERT INTO todos (id,name,status,priority,"
                "unmet_dependency_count,version,created_at,updated_at) "
                "SELECT unnest(cast(:ids as uuid[])), 'lattice', 'not_started', "
                "20, 0, 1, now(), now()"
            ),
            {"ids": [str(i) for layer in grid for i in layer]},
        )
        await session.execute(
            text(
                "INSERT INTO todo_dependencies (todo_id, depends_on_id) "
                "VALUES (cast(:a as uuid), cast(:b as uuid))"
            ),
            [
                {"a": str(a), "b": str(b)}
                for i in range(layers - 1)
                for a in grid[i]
                for b in grid[i + 1]
            ],
        )
        await session.commit()
        return grid

    async def test_a_wide_graph_does_not_explode(self, session):
        import time

        from app.repositories.dependency_repo import DependencyRepository

        layers, width = 20, 4
        grid = await self._lattice(session, layers, width)
        repo = DependencyRepository(session)

        started = time.perf_counter()
        path = await repo.find_cycle_path(grid[-1][0], grid[0][0])
        elapsed = time.perf_counter() - started

        assert path is not None, "the cycle was not detected"
        # Shortest route through the lattice is one node per layer. A longer
        # answer would mean the walk is wandering rather than breadth-first.
        assert len(path) == layers
        # Deliberately loose: the point is the difference between milliseconds
        # and never finishing, not a tight budget that flakes on a slow machine.
        assert elapsed < 2.0, f"took {elapsed:.1f}s over {width ** (layers - 2):,} paths"

    async def test_no_cycle_is_answered_just_as_fast(self, session):
        """The common case: the answer is 'no', over the same wide graph."""
        import time

        from app.repositories.dependency_repo import DependencyRepository

        grid = await self._lattice(session, 20, 4)
        repo = DependencyRepository(session)

        started = time.perf_counter()
        # Forwards along the layers, so nothing loops back.
        assert await repo.find_cycle_path(grid[0][0], grid[-1][0]) is None
        assert time.perf_counter() - started < 2.0
