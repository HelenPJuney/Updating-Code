"""
backend/livekit/kafka/token_service_import.py
──────────────────────────────────────────────────────────────────────────────
Thin re-export of LiveKit server coordinates so the kafka sub-package can
import them without creating a circular dependency with the livekit package.

The kafka sub-package's scheduler and worker_service both need LIVEKIT_URL,
LIVEKIT_API_KEY, and LIVEKIT_API_SECRET to build the QueueNotifier (DataChannel)
and worker JWT tokens. Rather than importing from ..token_service (which pulls
in FastAPI/router code), we read the same env vars independently here.
"""

import os

LIVEKIT_URL        = os.getenv("LIVEKIT_URL",        "ws://localhost:7880")
LIVEKIT_API_KEY    = os.getenv("LIVEKIT_API_KEY",    "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "devsecret")
