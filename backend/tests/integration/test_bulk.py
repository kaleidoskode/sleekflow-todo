"""Batch operations: partial success is the contract, not a fallback.

The behaviour these pin down is that a refused item fails *alone*. Everything
else — the routing, the per-item versions, the single event — exists to make
that true and stay true.
"""

import httpx
import pytest

from app.core.events import broker
from app.schemas.bulk import MAX_BULK_ITEMS


async def _create(client: httpx.AsyncClient, name: str) -> dict:
    response = await client.post("/api/todos", json={"name": name})
    assert response.status_code == 201
    return response.json()


def _ref(todo: dict) -> dict:
    return {"id": todo["id"], "version": todo["version"]}


async def _block(client: httpx.AsyncClient, dependent: dict, blocker: dict) -> None:
    response = await client.post(
        f"/api/todos/{dependent['id']}/dependencies", json={"depends_on_id": blocker["id"]}
    )
    assert response.status_code == 201


class TestRouting:
    async def test_bulk_path_is_not_swallowed_by_the_todo_id_route(
        self, client: httpx.AsyncClient
    ) -> None:
        """`/api/todos/bulk/status` also matches `/api/todos/{todo_id}/status`.

        Registered in the wrong order, `bulk` is parsed as a todo_id, fails UUID
        validation, and every request here becomes a 422 about the path — with
        nothing in the code to suggest why. This test fails loudly if the router
        include order in create_app is ever changed.
        """
        todo = await _create(client, "routed")
        response = await client.post(
            "/api/todos/bulk/status", json={"items": [_ref(todo)], "status": "in_progress"}
        )
        assert response.status_code == 200
        assert response.json()["succeeded"] == 1

    async def test_bulk_requires_a_token(self, anon_client: httpx.AsyncClient) -> None:
        response = await anon_client.post(
            "/api/todos/bulk/status", json={"items": [], "status": "completed"}
        )
        assert response.status_code == 401


class TestPartialSuccess:
    async def test_a_blocked_item_fails_alone(self, client: httpx.AsyncClient) -> None:
        """The headline behaviour: 2 of 3 succeed, and the batch still returns 200."""
        free_one = await _create(client, "free one")
        free_two = await _create(client, "free two")
        blocker = await _create(client, "blocker")
        blocked = await _create(client, "blocked")
        await _block(client, blocked, blocker)

        fresh = (await client.get(f"/api/todos/{blocked['id']}")).json()
        response = await client.post(
            "/api/todos/bulk/status",
            json={
                "items": [_ref(free_one), _ref(fresh), _ref(free_two)],
                "status": "in_progress",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert (body["succeeded"], body["failed"]) == (2, 1)

        by_id = {r["id"]: r for r in body["results"]}
        assert by_id[free_one["id"]]["ok"] is True
        assert by_id[free_two["id"]]["ok"] is True
        assert by_id[blocked["id"]]["ok"] is False
        assert by_id[blocked["id"]]["code"] == "BLOCKED_BY_DEPENDENCIES"

        # And the successes are durable, not rolled back with the failure.
        for todo in (free_one, free_two):
            current = (await client.get(f"/api/todos/{todo['id']}")).json()
            assert current["status"] == "in_progress"

    async def test_a_stale_version_fails_alone(self, client: httpx.AsyncClient) -> None:
        good = await _create(client, "good")
        stale = await _create(client, "stale")
        bumped = await client.patch(
            f"/api/todos/{stale['id']}",
            json={"name": "moved on"},
            headers={"If-Match": f'"{stale["version"]}"'},
        )
        assert bumped.status_code == 200

        response = await client.post(
            "/api/todos/bulk/status",
            json={"items": [_ref(good), _ref(stale)], "status": "in_progress"},
        )
        body = response.json()
        assert (body["succeeded"], body["failed"]) == (1, 1)
        by_id = {r["id"]: r for r in body["results"]}
        assert by_id[stale["id"]]["code"] == "VERSION_CONFLICT"
        # Optimistic concurrency survives batching: the stale item was refused,
        # not silently forced through.
        assert (await client.get(f"/api/todos/{stale['id']}")).json()["status"] == "not_started"

    async def test_a_missing_todo_fails_alone(self, client: httpx.AsyncClient) -> None:
        good = await _create(client, "good")
        ghost = {"id": "018f3b2c-0000-7000-8000-00000000dead", "version": 1}
        response = await client.post(
            "/api/todos/bulk/status",
            json={"items": [_ref(good), ghost], "status": "in_progress"},
        )
        body = response.json()
        assert (body["succeeded"], body["failed"]) == (1, 1)
        assert body["results"][1]["code"] == "NOT_FOUND"

    async def test_results_keep_the_order_they_were_sent(
        self, client: httpx.AsyncClient
    ) -> None:
        todos = [await _create(client, f"ordered {i}") for i in range(5)]
        response = await client.post(
            "/api/todos/bulk/status",
            json={"items": [_ref(t) for t in todos], "status": "in_progress"},
        )
        assert [r["id"] for r in response.json()["results"]] == [t["id"] for t in todos]

    async def test_successful_items_report_their_new_version(
        self, client: httpx.AsyncClient
    ) -> None:
        todo = await _create(client, "versioned")
        response = await client.post(
            "/api/todos/bulk/status", json={"items": [_ref(todo)], "status": "in_progress"}
        )
        result = response.json()["results"][0]
        # Returned so a client can act again without a refetch round trip.
        assert result["version"] == todo["version"] + 1


class TestBulkDelete:
    async def test_deletes_and_leaves_them_restorable(self, client: httpx.AsyncClient) -> None:
        todos = [await _create(client, f"doomed {i}") for i in range(3)]
        response = await client.post(
            "/api/todos/bulk/delete", json={"items": [_ref(t) for t in todos]}
        )
        assert response.json()["succeeded"] == 3

        listed = (await client.get("/api/todos")).json()["items"]
        assert all(t["id"] not in {x["id"] for x in listed} for t in todos)

        # Soft, not destroyed — the whole point of the delete model.
        restored = await client.post(
            f"/api/todos/{todos[0]['id']}/restore",
            headers={"If-Match": f'"{todos[0]["version"] + 1}"'},
        )
        assert restored.status_code == 200


class TestValidation:
    @pytest.mark.parametrize(
        ("payload", "fragment"),
        [
            ({"items": [], "status": "completed"}, "at least one"),
            (
                {
                    "items": [{"id": "018f3b2c-0000-7000-8000-000000000001", "version": 1}] * 2,
                    "status": "completed",
                },
                "more than once",
            ),
        ],
    )
    async def test_rejected_bodies(
        self, client: httpx.AsyncClient, payload: dict, fragment: str
    ) -> None:
        response = await client.post("/api/todos/bulk/status", json=payload)
        assert response.status_code == 422
        assert fragment in str(response.json()["errors"])

    async def test_over_the_item_limit(self, client: httpx.AsyncClient) -> None:
        items = [
            {"id": f"018f3b2c-0000-7000-8000-{i:012d}", "version": 1}
            for i in range(MAX_BULK_ITEMS + 1)
        ]
        response = await client.post(
            "/api/todos/bulk/status", json={"items": items, "status": "completed"}
        )
        assert response.status_code == 422
        assert str(MAX_BULK_ITEMS) in str(response.json()["errors"])

    async def test_messages_avoid_pydantic_jargon(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/todos/bulk/status", json={"items": [], "status": "completed"}
        )
        message = response.json()["errors"][0]["message"]
        assert not message.startswith("Value error")
        assert "List should have" not in message


class TestBatchAnnouncesOnce:
    async def test_one_event_for_the_whole_batch(self, client: httpx.AsyncClient) -> None:
        """Per-item events would cost every watching tab one refetch each — a
        single click turning into 200 rounds of invalidation per client."""
        todos = [await _create(client, f"batched {i}") for i in range(6)]
        async with broker.subscribe() as queue:
            response = await client.post(
                "/api/todos/bulk/status",
                json={"items": [_ref(t) for t in todos], "status": "in_progress"},
            )
            assert response.json()["succeeded"] == 6
        assert queue.qsize() == 1
        event = queue.get_nowait()
        assert event["action"] == "bulk_status_changed"
        assert event["count"] == 6
        assert event["status"] == "in_progress"
        assert event["actor"] == "fixture-user"

    async def test_a_batch_that_changed_nothing_stays_silent(
        self, client: httpx.AsyncClient
    ) -> None:
        todo = await _create(client, "stale only")
        async with broker.subscribe() as queue:
            response = await client.post(
                "/api/todos/bulk/status",
                json={"items": [{"id": todo["id"], "version": 99}], "status": "in_progress"},
            )
            assert response.json()["succeeded"] == 0
        assert queue.empty()
