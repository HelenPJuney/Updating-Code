"""
backend/livekit
──────────────────────────────────────────────────────────────────────────────
LiveKit-based communication layer — replaces the old aiortc WebRTC layer.
Now includes Kafka-based call scheduling, GPU-aware scaling, and SIP/PSTN
telephony support.

Integration in backend/app.py:

    # Minimal (no Kafka):
    from backend.livekit import livekit_router
    app.include_router(livekit_router)

    # Full (with Kafka producer lifecycle):
    from backend.livekit import livekit_router, kafka_health_router
    from backend.livekit.kafka.lifespan import kafka_lifespan
    app = FastAPI(lifespan=kafka_lifespan)
    app.include_router(livekit_router)
    app.include_router(kafka_health_router)   # /livekit/kafka/health + /metrics

    # With SIP support (set ENABLE_SIP=true):
    from backend.livekit import sip_router   # only available when ENABLE_SIP=true
    if sip_router:
        app.include_router(sip_router)

Endpoints:
    GET  /livekit/token              — issue JWT + submit to Kafka Scheduler
    GET  /livekit/health             — LiveKit + Kafka connectivity status
    GET  /livekit/queue-status/{id}  — poll queue position for a session
    GET  /livekit/kafka/health       — Kafka integration liveness check
    GET  /livekit/kafka/metrics      — Prometheus metrics

SIP Endpoints (when ENABLE_SIP=true):
    POST /sip/webhook                — LiveKit webhook receiver (SIP events)
    GET  /sip/health                 — SIP subsystem health check
    GET  /sip/sessions               — List active SIP sessions
    GET  /sip/session/{id}           — Lookup specific SIP session

Call flow (Kafka path — browser):
    Browser → GET /livekit/token
    Backend → produce call_request → Kafka → Call Scheduler
    Scheduler → GPU-aware assignment → Worker Service on GPU Node
    Worker Service → spawn ai_worker_task → LiveKit room.connect
    Worker  → VAD → STT → LLM → TTS → publish audio track
    Browser ← DataChannel: queue_update | call_start | greeting | transcript …

Call flow (SIP/PSTN path):
    PSTN → SIP Provider → Asterisk → LiveKit SIP → LiveKit Room
    LiveKit webhook → /sip/webhook → Kafka call_requests → Scheduler
    Scheduler → Worker Service → ai_worker_task → same AI pipeline
    SIP caller audio ↔ LiveKit ↔ AI worker audio

Call flow (fallback — no Kafka):
    Identical to original: ai_worker_task spawned directly by FastAPI.

Control channel (LiveKit DataChannel):
    Browser → Worker : { type: "interrupt" } | { type: "hangup" }
    Worker  → Browser: { type: "greeting" }  | { type: "transcript" }
                     | { type: "response" }  | { type: "barge_in" }
                     | { type: "error" }     | { type: "hangup" }
                     | { type: "queue_update", "position": N, "eta_sec": N }
                     | { type: "call_start" }
"""

from .ai_worker import livekit_router
from .kafka.health import kafka_health_router

# ── SIP/PSTN support (conditionally loaded via ENABLE_SIP env var) ────────────
sip_router = None
try:
    from .sip import ENABLE_SIP
    if ENABLE_SIP:
        from .sip import sip_router as _sip_router
        sip_router = _sip_router
except ImportError:
    pass

__all__ = ["livekit_router", "kafka_health_router", "sip_router"]
