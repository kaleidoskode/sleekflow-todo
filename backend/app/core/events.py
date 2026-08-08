"""In-process fan-out for server-sent events.

One ``asyncio.Queue`` per connected browser. Publishing never blocks and never
raises: it is called from request handlers *after* the transaction has
committed, so an exception here would fail a write that already succeeded.

Scope, stated plainly because it is the first thing an interviewer should ask:
this broker lives in the process. One uvicorn worker fans out correctly; two
workers each fan out to their own clients only. The fix is a shared bus —
Postgres ``LISTEN``/``NOTIFY`` (no new infrastructure, since the database is
already there) or Redis pub/sub. The publish/subscribe seam below is the only
thing that would change; nothing in the routers or the frontend would.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from app.models.todo import Todo

# Deliberately small. Every event carries the same instruction — "re-read the
# board" — so a backlog is redundant by construction: sixty queued events are
# worth exactly what one is. A client that falls this far behind is better
# served by dropping and acting on the next event than by replaying history.
QUEUE_MAXSIZE = 32


class EventBroker:
    """Fan-out to every open stream. Not durable, and not meant to be."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, event: dict[str, Any]) -> None:
        # list() because a subscriber can disconnect mid-iteration, which
        # mutates the set underneath us.
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # See QUEUE_MAXSIZE: dropping is the designed behaviour, not a
                # failure. A mutation must never wait on a slow browser.
                pass

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            # Runs on client disconnect, which reaches the generator as a
            # cancellation. Without this the set leaks a queue per closed tab.
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


broker = EventBroker()


def _envelope(action: str, actor: str) -> dict[str, Any]:
    """The fields every event carries, whatever it describes.

    Kept in one place because the frontend's `BoardEvent` declares `actor` and
    `at` as always present — a publisher that forgets one makes that type a
    lie rather than a contract.
    """
    return {"action": action, "actor": actor, "at": datetime.now(UTC).isoformat()}


def publish_todo_change(action: str, todo: Todo, actor: str, **extra: Any) -> None:
    """Announce a committed change to one todo.

    The event is a *signal*, not a payload: it carries enough to describe what
    happened in a toast, and deliberately not enough for a client to patch its
    cache from. Clients re-read through the normal endpoints instead. That
    keeps the server the single source of truth — applying an event body
    directly would let an out-of-order delivery overwrite newer state, which is
    exactly the lost update the whole versioning scheme exists to prevent.
    """
    broker.publish(
        _envelope(action, actor)
        | {
            "todo_id": str(todo.id),
            "name": todo.name,
            "version": todo.version,
            **extra,
        }
    )


def publish_dependency_change(action: str, todo_id: UUID, actor: str) -> None:
    """Edge changes have no single todo row, so they carry only the dependent."""
    broker.publish(_envelope(action, actor) | {"todo_id": str(todo_id)})


def publish_bulk_change(action: str, count: int, actor: str, **extra: Any) -> None:
    """One event for a whole batch, not one per item.

    Every event costs each watching tab a refetch, so announcing 200 individual
    changes would turn one click into 200 rounds of invalidation per client.
    A batch is a single thing that happened, so it is a single event. Callers
    are expected not to announce a batch that changed nothing.
    """
    broker.publish(_envelope(action, actor) | {"count": count, **extra})
