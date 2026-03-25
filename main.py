"""
main.py — FastAPI entry point for LiveKit + SIP backend.

Run:
    python main.py
    OR
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import os
import sys
from pathlib import Path

# Make sure livekit package is importable
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from livekit import livekit_router, kafka_health_router
from livekit.kafka.lifespan import kafka_lifespan

app = FastAPI(title="LiveKit AI Call Backend", version="1.0.0", lifespan=kafka_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Core routes ───────────────────────────────────────────────────────────────
app.include_router(livekit_router)       # /livekit/token, /livekit/health
app.include_router(kafka_health_router)  # /livekit/kafka/health, /metrics

# ── SIP / PSTN routes (only if ENABLE_SIP=true) ───────────────────────────────
from livekit import sip_router
if sip_router:
    app.include_router(sip_router)       # /sip/webhook, /sip/health, /sip/sessions
    print("✓ SIP/PSTN module enabled — /sip/webhook is live")
else:
    print("  SIP module disabled — set ENABLE_SIP=true to enable PSTN calls")

@app.get("/")
async def root():
    return {
        "status": "running",
        "endpoints": {
            "health":       "/livekit/health",
            "token":        "/livekit/token",
            "sip_health":   "/sip/health",
            "sip_webhook":  "/sip/webhook",
            "kafka_health": "/livekit/kafka/health",
        }
    }

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  LiveKit AI Backend")
    print("  http://localhost:8000")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
