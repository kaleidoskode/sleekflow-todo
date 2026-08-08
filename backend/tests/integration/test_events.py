"""The live-update path: mutations announce, the stream is gated, nothing blocks.

The end-to-end stream (browser holds an open request, receives frames) is
verified in the demo rather than here. What these tests pin down is the part
that silently rots: that every mutating route actually publishes, so a tab
watching the stream is not left stale by a route someone added later.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.events import QUEUE_MAXSIZE, EventBroker, broker
from app.routers.events import _frame, events


async def _create(client: httpx.AsyncClient, name: str = "watched") -> dict:
    response = await client.post("/api/todos", json={"name": name})
    assert response.status_code == 201
    return response.json()


async def _drain(queue: asyncio.Queue, expected: int) -> list[dict]:
    """Collect `expected` events, failing fast rather than hanging the suite."""
    events = []
    for _ in range(expected):
        events.append(await asyncio.wait_for(queue.get(), timeout=2.0))
    return events


class TestBroker:
    async def test_publish_reaches_every_subscriber(self) -> None:
        b = EventBroker()
        async with b.subscribe() as one, b.subscribe() as two:
            b.publish({"action": "created"})
            assert one.get_nowait() == {"action": "created"}
            assert two.get_nowait() == {"action": "created"}

    async def test_unsubscribes_on_exit(self) -> None:
        b = EventBroker()
        async with b.subscribe():
            assert b.subscriber_count == 1
        assert b.subscriber_count == 0

    async def test_a_full_queue_drops_rather_than_blocking(self) -> None:
        """A mutation must never wait on a browser that stopped reading."""
        b = EventBroker()
        async with b.subscribe() as queue:
            for i in range(QUEUE_MAXSIZE + 25):
                b.publish({"n": i})
            assert queue.qsize() == QUEUE_MAXSIZE

    async def test_publish_survives_a_subscriber_leaving_mid_fanout(self) -> None:
        b = EventBroker()
        async with b.subscribe() as queue:
            pass  # unsubscribed, but we still hold the queue
        b.publish({"action": "created"})  # must not raise
        assert queue.empty()


class TestStreamFraming:
    def test_frame_is_valid_sse(self) -> None:
        frame = _frame("todo", {"action": "created", "name": "x"})
        assert frame.startswith("event: todo\ndata: ")
        assert frame.endswith("\n\n")

    def test_data_is_one_line(self) -> None:
        """A newline inside `data:` would split the frame into two events."""
        frame = _frame("todo", {"name": "multi\nline"})
        body = frame.removeprefix("event: todo\ndata: ").removesuffix("\n\n")
        assert "\n" not in body
        assert json.loads(body)["name"] == "multi\nline"


class TestStreamIsGated:
    async def test_stream_requires_a_token(self, anon_client: httpx.AsyncClient) -> None:
        response = await anon_client.get("/api/events")
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"


class TestStreamDelivery:
    """The generator behind the stream, driven directly.

    Not through the test client: `httpx.ASGITransport` accumulates the whole
    response body before returning, so requesting an endpoint that never
    finishes hangs forever. Pulling from `body_iterator` exercises exactly the
    same code the server sends, without a transport that cannot represent it.
    """

    @staticmethod
    async def _open() -> tuple[object, object]:
        request = SimpleNamespace(is_disconnected=lambda: _never_disconnected())
        response = await events(request, SimpleNamespace(username="watcher"))
        return response, response.body_iterator

    async def test_response_advertises_an_event_stream(self) -> None:
        response, iterator = await self._open()
        try:
            assert response.media_type == "text/event-stream"
            assert response.headers["cache-control"] == "no-cache, no-transform"
            assert response.headers["x-accel-buffering"] == "no"
        finally:
            await iterator.aclose()

    async def test_ready_frame_then_a_published_event(self) -> None:
        _, iterator = await self._open()
        try:
            name, payload = _parse(await asyncio.wait_for(anext(iterator), timeout=3.0))
            assert name == "ready"
            assert payload["subscribers"] >= 1

            # Straight to the broker: the routes are covered above, this is
            # about what reaches the wire.
            broker.publish({"action": "updated", "todo_id": "abc", "actor": "grace"})

            name, payload = _parse(await asyncio.wait_for(anext(iterator), timeout=3.0))
            assert name == "todo"
            assert payload == {"action": "updated", "todo_id": "abc", "actor": "grace"}
        finally:
            await iterator.aclose()

    async def test_closing_the_stream_unsubscribes(self) -> None:
        """Every closed tab must drop its queue, or the broker leaks one per
        reconnect and fans out to an ever-growing set of dead readers."""
        before = broker.subscriber_count
        _, iterator = await self._open()
        await asyncio.wait_for(anext(iterator), timeout=3.0)
        assert broker.subscriber_count == before + 1

        await iterator.aclose()
        assert broker.subscriber_count == before


async def _never_disconnected() -> bool:
    return False


def _parse(frame: str) -> tuple[str, dict]:
    name, payload = "", {}
    for line in frame.split("\n"):
        if line.startswith("event:"):
            name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            payload = json.loads(line.removeprefix("data:").strip())
    return name, payload


class TestMutationsPublish:
    async def test_create_publishes(self, client: httpx.AsyncClient) -> None:
        async with broker.subscribe() as queue:
            todo = await _create(client, "announce me")
        (event,) = await _drain(queue, 1)
        assert event["action"] == "created"
        assert event["todo_id"] == todo["id"]
        assert event["name"] == "announce me"
        assert event["actor"] == "fixture-user"

    async def test_update_publishes_the_new_version(self, client: httpx.AsyncClient) -> None:
        todo = await _create(client)
        async with broker.subscribe() as queue:
            response = await client.patch(
                f"/api/todos/{todo['id']}",
                json={"name": "renamed"},
                headers={"If-Match": f'"{todo["version"]}"'},
            )
        assert response.status_code == 200
        (event,) = await _drain(queue, 1)
        assert event["action"] == "updated"
        assert event["version"] == todo["version"] + 1

    async def test_status_change_publishes(self, client: httpx.AsyncClient) -> None:
        todo = await _create(client)
        async with broker.subscribe() as queue:
            response = await client.post(
                f"/api/todos/{todo['id']}/status",
                json={"status": "in_progress"},
                headers={"If-Match": f'"{todo["version"]}"'},
            )
        assert response.status_code == 200
        (event,) = await _drain(queue, 1)
        assert event["action"] == "status_changed"
        assert event["status"] == "in_progress"

    async def test_delete_and_restore_both_publish(self, client: httpx.AsyncClient) -> None:
        todo = await _create(client)
        async with broker.subscribe() as queue:
            deleted = await client.delete(
                f"/api/todos/{todo['id']}", headers={"If-Match": f'"{todo["version"]}"'}
            )
            assert deleted.status_code == 204
            restored = await client.post(
                f"/api/todos/{todo['id']}/restore",
                headers={"If-Match": f'"{todo["version"] + 1}"'},
            )
            assert restored.status_code == 200
        actions = [e["action"] for e in await _drain(queue, 2)]
        assert actions == ["deleted", "restored"]

    async def test_dependency_edges_publish(self, client: httpx.AsyncClient) -> None:
        """An edge flips is_blocked without touching the todo row, so it needs
        its own event or watching tabs never learn the task became blocked."""
        blocker = await _create(client, "blocker")
        dependent = await _create(client, "dependent")
        async with broker.subscribe() as queue:
            added = await client.post(
                f"/api/todos/{dependent['id']}/dependencies",
                json={"depends_on_id": blocker["id"]},
            )
            assert added.status_code == 201
            removed = await client.delete(
                f"/api/todos/{dependent['id']}/dependencies/{blocker['id']}"
            )
            assert removed.status_code == 204
        events = await _drain(queue, 2)
        assert [e["action"] for e in events] == ["dependency_added", "dependency_removed"]
        assert all(e["todo_id"] == dependent["id"] for e in events)

    async def test_recurring_completion_announces_the_spawned_occurrence(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/api/todos",
            json={
                "name": "weekly report",
                "due_date": "2026-09-01T09:00:00Z",
                "recurrence_unit": "week",
                "recurrence_interval": 1,
            },
        )
        assert response.status_code == 201
        todo = response.json()

        async with broker.subscribe() as queue:
            completed = await client.post(
                f"/api/todos/{todo['id']}/status",
                json={"status": "completed"},
                headers={"If-Match": f'"{todo["version"]}"'},
            )
        assert completed.status_code == 200
        assert completed.json()["next_occurrence"] is not None

        events = await _drain(queue, 2)
        assert [e["action"] for e in events] == ["status_changed", "created"]
        assert events[1]["todo_id"] == completed.json()["next_occurrence"]["id"]


class TestFailedMutationsStaySilent:
    """A rejected write changed nothing, so announcing it would make every
    watching tab refetch for no reason — and imply a change that never landed."""

    @pytest.mark.parametrize(
        ("name", "headers", "expected"),
        [
            ("stale version", {"If-Match": '"999"'}, 409),
            ("missing precondition", {}, 428),
        ],
    )
    async def test_no_event_on_rejected_update(
        self, client: httpx.AsyncClient, name: str, headers: dict, expected: int
    ) -> None:
        todo = await _create(client)
        async with broker.subscribe() as queue:
            response = await client.patch(
                f"/api/todos/{todo['id']}", json={"name": "nope"}, headers=headers
            )
            assert response.status_code == expected
            await asyncio.sleep(0)  # let any stray publish land before asserting
        assert queue.empty()

    async def test_no_event_when_a_transition_is_blocked(
        self, client: httpx.AsyncClient
    ) -> None:
        blocker = await _create(client, "blocker")
        dependent = await _create(client, "dependent")
        added = await client.post(
            f"/api/todos/{dependent['id']}/dependencies",
            json={"depends_on_id": blocker["id"]},
        )
        assert added.status_code == 201

        current = (await client.get(f"/api/todos/{dependent['id']}")).json()
        async with broker.subscribe() as queue:
            response = await client.post(
                f"/api/todos/{dependent['id']}/status",
                json={"status": "in_progress"},
                headers={"If-Match": f'"{current["version"]}"'},
            )
            assert response.status_code == 422
            await asyncio.sleep(0)
        assert queue.empty()
