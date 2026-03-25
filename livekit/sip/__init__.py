"""
backend/livekit/sip/__init__.py
──────────────────────────────────────────────────────────────────────────────
SIP/PSTN integration layer for the LiveKit AI call center.

Provides:
    sip_router          — FastAPI router with /sip/webhook endpoint
    SipSessionManager   — SIP call_id ↔ session_id ↔ room_id mapping
    SipEventHandler     — Processes LiveKit room events for SIP calls

Call flow (SIP path):
    PSTN → SIP Provider → Asterisk → LiveKit SIP → LiveKit Room
    LiveKit webhook → sip_ingress → Kafka call_requests → Scheduler → Worker

Feature flag:
    Set ENABLE_SIP=true to activate (default: false).
"""

from .sip_config import ENABLE_SIP

if ENABLE_SIP:
    from .sip_ingress import sip_router
    from .sip_session_manager import sip_session_mgr, SipSessionManager
    from .sip_event_handler import SipEventHandler

    __all__ = ["sip_router", "sip_session_mgr", "SipSessionManager", "SipEventHandler", "ENABLE_SIP"]
else:
    __all__ = ["ENABLE_SIP"]
