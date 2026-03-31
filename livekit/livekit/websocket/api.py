"""
websocket/api.py
────────────────────────────────────────────────────────────────────────────────
WebSocket + Server-Sent Events (SSE) API for real-time dashboard updates.

Endpoints:
    WS   /ws/events          — WebSocket stream (preferred)
    GET  /ws/stream          — SSE fallback for browsers that can't WS
    GET  /ws/history         — last N events (JSON REST)
    GET  /ws/stats           — hub statistics
    POST /ws/publish         — manually publish a test event (dev use)

WebSocket protocol:
    Client connects → receives history replay (last 100 events)
    Server sends: JSON event object on every publish()
    Client can send: {"type": "ping"} → server replies {"type": "pong"}
    Client can send: {"type": "subscribe", "filter": ["call_started", ...]}
                     → future events are filtered (optional)
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

logger = logging.getLogger("callcenter.websocket.api")

ws_router = APIRouter(prefix="/ws", tags=["websocket"])


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@ws_router.websocket("/events")
async def ws_events(websocket: WebSocket):
    """
    Full-duplex WebSocket stream of call-center events.
    Replays last 100 events to new connections.
    """
    from . import event_hub

    await websocket.accept()
    queue = await event_hub.subscribe(replay_history=True)
    active_filter: Optional[Set[str]] = None  # None = no filter (all events)
    logger.info("[WS] client connected  total=%d", event_hub.subscriber_count)

    async def _sender():
        """Push events from queue to WebSocket."""
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                if active_filter and event.get("type") not in active_filter:
                    continue
                await websocket.send_text(json.dumps(event))
            except asyncio.TimeoutError:
                # Send keepalive ping
                try:
                    await websocket.send_text(json.dumps({"type": "ping", "ts": time.time()}))
                except Exception:
                    return
            except Exception:
                return

    async def _receiver():
        """Process messages from the client."""
        nonlocal active_filter
        while True:
            try:
                data = await websocket.receive_text()
                msg  = json.loads(data)
                mtype = msg.get("type", "")
                if mtype == "ping":
                    await websocket.send_text(json.dumps({"type": "pong", "ts": time.time()}))
                elif mtype == "subscribe":
                    filters = msg.get("filter", [])
                    active_filter = set(filters) if filters else None
                elif mtype == "unsubscribe":
                    active_filter = None
            except WebSocketDisconnect:
                return
            except Exception:
                return

    try:
        sender_task   = asyncio.create_task(_sender())
        receiver_task = asyncio.create_task(_receiver())
        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("[WS] connection error: %s", exc)
    finally:
        await event_hub.unsubscribe(queue)
        logger.info("[WS] client disconnected  total=%d", event_hub.subscriber_count)


# ── SSE fallback ─────────────────────────────────────────────────────────────

@ws_router.get("/stream")
async def sse_stream():
    """
    Server-Sent Events fallback for clients that cannot use WebSocket.
    Usage:  const es = new EventSource('/ws/stream');
            es.onmessage = e => console.log(JSON.parse(e.data));
    """
    from . import event_hub

    async def _generator():
        queue = await event_hub.subscribe(replay_history=False)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type':'ping','ts':time.time()})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await event_hub.unsubscribe(queue)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── REST helpers ─────────────────────────────────────────────────────────────

@ws_router.get("/history")
async def get_history(n: int = 50):
    """Get the last N events from the hub history."""
    from . import event_hub
    events = list(event_hub._history)
    return {
        "events": events[-n:] if n < len(events) else events,
        "count":  min(n, len(events)),
        "total_history": len(events),
        "timestamp": time.time(),
    }


@ws_router.get("/stats")
async def hub_stats():
    """WebSocket hub statistics."""
    from . import event_hub
    return {**event_hub.stats(), "timestamp": time.time()}


@ws_router.post("/publish")
async def manual_publish(event: Dict[str, Any]):
    """
    Manually publish an event (dev/testing only).
    In production, events are published by the backend services.
    """
    from . import event_hub
    if "type" not in event:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="event.type is required")
    await event_hub.publish(event)
    return {"status": "published", "event_type": event["type"]}
