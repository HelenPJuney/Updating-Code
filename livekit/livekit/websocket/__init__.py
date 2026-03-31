"""
livekit/websocket
────────────────────────────────────────────────────────────────────────────────
Real-time event hub + WebSocket API.

Usage:
    from livekit.websocket import event_hub, ws_router
    app.include_router(ws_router)     # /ws/events (WebSocket)
                                      # /ws/stream  (SSE fallback)

    # Publish an event from anywhere in the backend:
    await event_hub.publish({"type": "call_started", "session_id": "..."})
"""

from .hub import EventHub
from .api import ws_router

# Module-level singleton
event_hub = EventHub()

__all__ = ["event_hub", "ws_router", "EventHub"]
