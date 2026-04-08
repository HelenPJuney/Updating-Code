# [ PHASE 1: BOOTSTRAP & PATHING ]
# * Sets UTF-8 encoding for clean console logs.
# * Loads .env configuration for database/Kafka credentials.
# * Prioritizes venv site-packages to ensure SDK compatibility.
#       |
#       v
# [ PHASE 2: LIFESPAN STARTUP ]
# (Orchestrating background service boot-order)
#       |
#       |----> Kafka Producer: Connects to message broker.
#       |----> Routing Engine: Loads call distribution rules.
#       |----> Scheduling Svc: Starts job polling and SQLite.
#       |----> Integration Svc: Connects external app hooks.
#       |----> IVR Pre-warm: Caches TTS greetings for speed.
#       |----> Call Center DB: Initializes Postgres tables.
#       |----> Outbound Monitor: Starts callback watch-loop.
#       |----> Queue Kafka: Persists caller queues to Kafka.
#       v
# [ PHASE 3: API ROUTING ]
# (Mapping specialized feature routers)
#       |
#       |--- Includes v3.0 Call Center, AI Assist, and WebSockets.
#       |--- Conditionally enables IVR and SIP/PSTN modules.
#       v
# [ PHASE 4: REQUEST HANDLING ]
# (Active server responding to client calls)
#       |
#       |--- root(): Overview of system and endpoints.
#       |--- system_health(): Deep-check of all components.
#       |--- call_test_page(): Serves the test frontend HTML.
#       v
# [ PHASE 5: LIFESPAN SHUTDOWN ]
# (Safe termination and resource release)
#       |
#       `----> Stops Queue Kafka, Outbound Monitor, DB, and Producer.

import os
import sys

# Fix Windows console encoding for Unicode characters (checkmarks etc)
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from contextlib import asynccontextmanager
from pathlib import Path

# Load .env file BEFORE anything else reads os.getenv()
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

# Put venv site-packages FIRST so installed livekit SDK (livekit.api, livekit.rtc)
# takes priority over the local livekit/ folder for SDK imports.
# The local livekit/ folder is still importable as a package via sys.path[1].
_here = Path(__file__).parent
_venv_site = _here / "venv" / "Lib" / "site-packages"
if _venv_site.exists():
    sys.path.insert(0, str(_venv_site))
sys.path.insert(1 if _venv_site.exists() else 0, str(_here))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from livekit import livekit_router, kafka_health_router

# ── New feature modules ────────────────────────────────────────────────────────
from livekit.routing import routing_engine, routing_router
from livekit.scheduling import scheduling_service, scheduling_router
from livekit.websocket import event_hub, ws_router
from livekit.offline import offline_handler
from livekit.integration import integration_router, integration_service
from livekit.ai_assist import ai_assist_router
from livekit.receiver import receiver_router, tts_router

# ── Call Center v3.0 ──────────────────────────────────────────────────────────
from livekit.callcenter.api import cc_router
from livekit.callcenter import db as callcenter_db
from livekit.callcenter.outbound_engine import start_outbound_monitor, stop_outbound_monitor
from livekit.callcenter.email_scheduler import start_email_scheduler, stop_email_scheduler
from livekit.callcenter.queue_engine import start_kafka as start_queue_kafka, stop_kafka as stop_queue_kafka

# ── IVR Routing Module ────────────────────────────────────────────────────────
try:
    from livekit.routing_ivr.ivr_agent import ivr_router
    from livekit.routing_ivr.tts_engine import pre_warm as ivr_pre_warm
    _IVR_AVAILABLE = True
except ImportError as _ivr_err:
    _IVR_AVAILABLE = False
    ivr_router = None
    print(f"  IVR module not loaded: {_ivr_err}")

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

    # ── Integration service ──────────────────────────────────────────────────
    try:
        await integration_service.start()
        print("✓ Integration service started")
    except Exception as exc:
        print(f"  Integration service failed to start: {exc}")

    # ── IVR TTS pre-warm (pre-synthesize greeting phrases) ────────────────────
    if _IVR_AVAILABLE:
        try:
            await ivr_pre_warm()
            print("✓ IVR TTS pre-warm complete")
        except Exception as exc:
            print(f"  IVR TTS pre-warm failed: {exc}")

    # ── Call Center DB + Outbound monitor + Email scheduler ──────────────────
    try:
        await callcenter_db.init_db()
        # Load DB-backed business hours config into memory
        from livekit.callcenter.business_hours import load_config_from_db
        from livekit.callcenter.email_service import load_email_config_from_db
        await load_config_from_db()
        await load_email_config_from_db()
        await start_outbound_monitor()
        await start_email_scheduler()
        print("✓ Call Center DB initialized + Outbound monitor + Email scheduler started")
    except Exception as exc:
        print(f"  Call Center DB init failed: {exc}")

    # ── Queue Engine Kafka (persist caller queue to Kafka) ───────────────────
    try:
        await start_queue_kafka()
        print("✓ Queue Engine Kafka started")
    except Exception as exc:
        print(f"  Queue Engine Kafka failed (using in-memory fallback): {exc}")

    yield  # ── application runs ──────────────────────────────────────────────

    # ── Shutdown ─────────────────────────────────────────────────────────────
    await stop_queue_kafka()
    await stop_outbound_monitor()
    await stop_email_scheduler()
    await callcenter_db.close_db()
    await integration_service.stop()
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
app.include_router(integration_router, prefix="/integration") # Feature 1: External App Integration
app.include_router(ai_assist_router)     # Feature 2: AI Auto-join
app.include_router(receiver_router)      # Receiver (Helen) token endpoints
app.include_router(tts_router)           # /tts/speak — Piper TTS injection
app.include_router(cc_router)            # /cc/* — New call center v3.0 endpoints
if _IVR_AVAILABLE and ivr_router:
    app.include_router(ivr_router)       # /ivr/* — Intelligent IVR routing
    print("✓ IVR routing module enabled — /ivr/process is live")

# ── SIP / PSTN routes (only if ENABLE_SIP=true) ────────────────────────────────
from livekit import sip_router
if sip_router:
    app.include_router(sip_router)       # /sip/webhook, /sip/health, /sip/sessions
    print("✓ SIP/PSTN module enabled — /sip/webhook is live")
else:
    print("  SIP module disabled — set ENABLE_SIP=true to enable PSTN calls")


@app.get("/call-test", include_in_schema=False)
async def call_test_page():
    """Serve the call-test frontend HTML page."""
    from pathlib import Path
    html_path = Path(__file__).parent / "call-test" / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    return {"error": "call-test/index.html not found"}


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
