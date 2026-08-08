"""Server-sent events, so open tabs learn about each other's writes.

Chosen over WebSockets because the traffic is one-directional — the server
announces, the client never replies — and SSE is plain HTTP: it needs no
protocol upgrade, no extra infrastructure, and reconnects are a normal request
rather than a new transport to babysit.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.deps import streaming_user
from app.core.events import broker
from app.models.user import User

router = APIRouter(tags=["events"])

# Long-lived idle connections get reaped by proxies and load balancers. A
# comment frame every 20s is valid SSE that the browser ignores, and it keeps
# the connection classified as active.
HEARTBEAT_SECONDS = 20.0


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.get(
    "/api/events",
    summary="Subscribe to live board changes",
    description=(
        "A `text/event-stream` of committed changes, so every open tab stays "
        "current without polling. Each frame names the action, the todo and "
        "the person responsible.\n\n"
        "Events are **signals, not state**: on receiving one a client re-reads "
        "through the normal endpoints rather than patching its cache from the "
        "body. That keeps optimistic concurrency the only path that writes.\n\n"
        "Not usable from this page — Swagger cannot render a stream, and "
        "`EventSource` cannot send an `Authorization` header, so the frontend "
        "reads it with `fetch` and a `ReadableStream`."
    ),
    responses={
        200: {
            "description": "An open event stream.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        401: {"description": "Missing, malformed, or expired token."},
    },
)
async def events(request: Request, user: User = Depends(streaming_user)) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        async with broker.subscribe() as queue:
            # Sent immediately so the client can distinguish "connected" from
            # "connecting but the server never answered" — the frontend flips
            # its live indicator on this, not on the fetch resolving.
            yield _frame("ready", {"subscribers": broker.subscriber_count})

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    # A disconnect normally arrives as cancellation, but a
                    # client that vanished without a FIN is only detectable by
                    # writing to it, which is what the heartbeat does.
                    if await request.is_disconnected():
                        break
                    yield ": keep-alive\n\n"
                    continue
                yield _frame("todo", event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which would hold
            # every event until the buffer filled and defeat the point.
            "X-Accel-Buffering": "no",
        },
    )
