"""
main.py — FastAPI entry point for LiveKit + SIP backend.

Run:
    python main.py
    OR
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Make sure livekit package is importable
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from livekit import livekit_router, kafka_health_router

# ── New feature modules ────────────────────────────────────────────────────────
from livekit.routing import routing_engine, routing_router
from livekit.scheduling import scheduling_service, scheduling_router
from livekit.websocket import event_hub, ws_router
from livekit.offline import offline_handler


# ── Extended lifespan: start/stop all services ────────────────────────────────
@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """Start all background services on startup; shut them down on exit."""
    # ── Kafka producer (existing) ────────────────────────────────────────────
    from livekit.kafka.producer import get_producer
    producer = get_producer()
    await producer.start()

    # ── Routing rules (load from disk) ───────────────────────────────────────
    routing_engine.load_rules()

    # ── Scheduling service (start SQLite + poll loop) ────────────────────────
    try:
        await scheduling_service.start()
        print("✓ Scheduling service started")
    except Exception as exc:
        print(f"  Scheduling service failed to start: {exc}")

    # ── Publish initial system status to WebSocket hub ───────────────────────
    await event_hub.publish_system_status(
        online=True, active_nodes=0, queue_depth=0
    )

    yield  # ── application runs ──────────────────────────────────────────────

    # ── Shutdown ─────────────────────────────────────────────────────────────
    await scheduling_service.stop()
    await producer.stop()
    print("  All services stopped")


app = FastAPI(
    title="LiveKit AI Call Backend",
    version="2.0.0",
    description="Production-grade AI call center: routing, scheduling, retries, WebSocket",
    lifespan=app_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Core routes ───────────────────────────────────────────────────────────────
app.include_router(livekit_router)       # /livekit/token, /livekit/health
app.include_router(kafka_health_router)  # /livekit/kafka/health, /metrics

# ── NEW: feature routes ───────────────────────────────────────────────────────
app.include_router(routing_router)       # /routing/rules, /routing/agents, /routing/decision
app.include_router(scheduling_router)    # /scheduling/jobs, /scheduling/stats
app.include_router(ws_router)            # /ws/events (WebSocket), /ws/stream (SSE)

# ── SIP / PSTN routes (only if ENABLE_SIP=true) ────────────────────────────────
from livekit import sip_router
if sip_router:
    app.include_router(sip_router)       # /sip/webhook, /sip/health, /sip/sessions
    print("✓ SIP/PSTN module enabled — /sip/webhook is live")
else:
    print("  SIP module disabled — set ENABLE_SIP=true to enable PSTN calls")


@app.get("/")
async def root():
    node_summary = offline_handler.get_node_summary()
    return {
        "status":  "running",
        "version": "2.0.0",
        "endpoints": {
            "health":       "/livekit/health",
            "token":        "/livekit/token",
            "kafka_health": "/livekit/kafka/health",
            "routing":      "/routing/rules",
            "agents":       "/routing/agents",
            "scheduling":   "/scheduling/jobs",
            "websocket":    "/ws/events",
            "sse":          "/ws/stream",
            "sip_health":   "/sip/health",
            "sip_webhook":  "/sip/webhook",
        },
        "system": {
            "active_nodes":   len([n for n in node_summary if n["alive"]]),
            "ws_subscribers": event_hub.subscriber_count,
        },
    }


@app.get("/health")
async def system_health():
    """Combined system health check."""
    from livekit.kafka.producer import get_producer
    from livekit.session_manager import livekit_session_manager

    producer      = get_producer()
    node_summary  = offline_handler.get_node_summary()
    offline_status = await offline_handler.check_status()
    sched_stats   = await scheduling_service.stats()

    return {
        "status":          "ok",
        "kafka_active":    producer.is_kafka_active,
        "offline_status":  offline_status.value,
        "active_sessions": livekit_session_manager.count,
        "active_nodes":    len([n for n in node_summary if n["alive"]]),
        "nodes":           node_summary,
        "scheduling":      sched_stats,
        "ws_subscribers":  event_hub.subscriber_count,
        "routing_rules":   len(routing_engine.rules_snapshot()),
    }


if __name__ == "__main__":
    import uvicorn
    print("=" * 55)
    print("  LiveKit AI Backend v2.0")
    print("  http://localhost:8000")
    print("  WebSocket: ws://localhost:8000/ws/events")
    print("  SSE:       http://localhost:8000/ws/stream")
    print("=" * 55)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
