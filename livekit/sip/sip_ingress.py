"""
SIP Ingress Service — FastAPI webhook endpoint for LiveKit SIP events.

This module provides the /sip/* routes that LiveKit's webhook system calls
when SIP-related room events occur.

Endpoints:
    POST /sip/webhook         — Receive LiveKit webhook events (room, participant)
    GET  /sip/health          — SIP subsystem health check
    GET  /sip/sessions        — List active SIP sessions
    GET  /sip/session/{id}    — Lookup a specific SIP session
    GET  /sip/metrics         — SIP-specific metrics

Security:
    • HMAC-SHA256 webhook signature validation
    • Rate limiting (configurable window + max)
    • Caller allowlist (SIP_ALLOWED_CALLERS)
    • Production signature enforcement (SIP_ENFORCE_SIGNATURE)

LiveKit Webhook Flow:
    1. LiveKit SIP receives INVITE → creates room → joins SIP participant
    2. LiveKit fires webhook to LIVEKIT_WEBHOOK_URL
    3. Our /sip/webhook receives the event payload
    4. SipEventHandler processes the event → triggers Kafka call scheduling

    To configure LiveKit to send webhooks, add to livekit.yaml:
        webhook:
          urls:
            - http://your-backend:8000/sip/webhook
          api_key: devkey
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, Request, HTTPException

from .sip_config import (
    ENABLE_SIP,
    SIP_WEBHOOK_SECRET,
    SIP_ENFORCE_SIGNATURE,
    SIP_RATE_LIMIT_MAX,
    SIP_RATE_LIMIT_WINDOW,
    SIP_ALLOWED_CALLERS,
    SIP_PARTICIPANT_PREFIX,
)
from .sip_event_handler import SipEventHandler
from .sip_session_manager import sip_session_mgr, SipCallState

logger = logging.getLogger("callcenter.sip.ingress")

# ── FastAPI Router ────────────────────────────────────────────────────────────
sip_router = APIRouter(prefix="/sip", tags=["sip"])


# ══════════════════════════════════════════════════════════════════════════════
# Rate Limiter (simple sliding-window in-process)
# ══════════════════════════════════════════════════════════════════════════════

class _SlidingWindowRateLimiter:
    """
    Simple in-process sliding-window rate limiter.
    Suitable for single-instance deployments.  For multi-instance,
    replace with Redis-based rate limiting.
    """

    def __init__(self, max_requests: int, window_sec: float) -> None:
        self._max = max_requests
        self._window = window_sec
        self._timestamps: deque[float] = deque()

    def allow(self) -> bool:
        """Return True if request is allowed, False if rate-limited."""
        now = time.time()
        # Evict expired timestamps
        while self._timestamps and self._timestamps[0] < now - self._window:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._max:
            return False
        self._timestamps.append(now)
        return True

    @property
    def current_count(self) -> int:
        now = time.time()
        while self._timestamps and self._timestamps[0] < now - self._window:
            self._timestamps.popleft()
        return len(self._timestamps)


_rate_limiter = _SlidingWindowRateLimiter(SIP_RATE_LIMIT_MAX, SIP_RATE_LIMIT_WINDOW)

# ── Metrics counters ──────────────────────────────────────────────────────────
_metrics = {
    "webhooks_received": 0,
    "webhooks_rate_limited": 0,
    "webhooks_signature_failed": 0,
    "webhooks_caller_blocked": 0,
    "calls_initiated": 0,
    "calls_completed": 0,
    "calls_failed": 0,
}


# ══════════════════════════════════════════════════════════════════════════════
# Webhook Endpoint
# ══════════════════════════════════════════════════════════════════════════════

@sip_router.post("/webhook")
async def sip_webhook(request: Request):
    """
    Receive LiveKit webhook events.

    LiveKit sends these events as JSON POST requests:
        • room_started          — new room created
        • room_finished         — room destroyed
        • participant_joined    — someone joins a room
        • participant_left      — someone leaves a room
        • track_published       — track published (ignored)

    For SIP calls, the lifecycle is:
        1. room_started (LiveKit SIP creates room from INVITE)
        2. participant_joined (SIP caller enters room)
        3. participant_left (SIP caller hangs up / BYE)
        4. room_finished (room destroyed after last participant leaves)
    """
    if not ENABLE_SIP:
        raise HTTPException(status_code=503, detail="SIP integration disabled")

    _metrics["webhooks_received"] += 1

    # ── Rate limiting ─────────────────────────────────────────────────────────
    if not _rate_limiter.allow():
        _metrics["webhooks_rate_limited"] += 1
        logger.warning("[SipWebhook] rate limited — dropping request")
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for SIP webhooks",
        )

    # ── Read and validate webhook payload ─────────────────────────────────────
    body = await request.body()

    # Validate webhook signature
    if SIP_WEBHOOK_SECRET:
        auth_header = request.headers.get("Authorization", "")
        if not _validate_webhook_signature(body, auth_header, SIP_WEBHOOK_SECRET):
            _metrics["webhooks_signature_failed"] += 1
            if SIP_ENFORCE_SIGNATURE:
                logger.warning("[SipWebhook] invalid signature — rejecting (production mode)")
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
            else:
                logger.warning("[SipWebhook] invalid signature — processing anyway (dev mode)")

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("event", "")
    room_info = payload.get("room", {})
    participant_info = payload.get("participant", {})

    room_name = room_info.get("name", "")
    room_sid = room_info.get("sid", "")
    participant_identity = participant_info.get("identity", "")

    logger.info(
        "[SipWebhook] event=%s  room=%s  participant=%s",
        event_type,
        room_name[:12] if room_name else "n/a",
        participant_identity[:16] if participant_identity else "n/a",
    )

    # ── Caller allowlist check ────────────────────────────────────────────────
    if (
        event_type == "participant_joined"
        and participant_identity.startswith(SIP_PARTICIPANT_PREFIX)
        and SIP_ALLOWED_CALLERS
    ):
        caller_number = participant_identity.removeprefix(SIP_PARTICIPANT_PREFIX)
        if caller_number not in SIP_ALLOWED_CALLERS:
            _metrics["webhooks_caller_blocked"] += 1
            logger.warning(
                "[SipWebhook] blocked caller not in allowlist: %s", caller_number
            )
            # Return 200 so LiveKit doesn't retry, but don't process
            return {"status": "blocked", "reason": "caller_not_allowed", "timestamp": time.time()}

    # ── Route to handler ──────────────────────────────────────────────────────
    try:
        if event_type == "room_started":
            await SipEventHandler.on_room_started(
                room_name=room_name,
                room_sid=room_sid,
            )

        elif event_type == "participant_joined":
            result = await SipEventHandler.on_participant_joined(
                room_name=room_name,
                room_sid=room_sid,
                participant_identity=participant_identity,
                participant_sid=participant_info.get("sid", ""),
                participant_metadata=participant_info.get("metadata", ""),
            )
            if result:
                _metrics["calls_initiated"] += 1

        elif event_type == "participant_left":
            await SipEventHandler.on_participant_left(
                room_name=room_name,
                participant_identity=participant_identity,
                participant_sid=participant_info.get("sid", ""),
            )
            # Check if this was a SIP caller leaving
            if participant_identity.startswith(SIP_PARTICIPANT_PREFIX):
                _metrics["calls_completed"] += 1

        elif event_type == "room_finished":
            await SipEventHandler.on_room_finished(
                room_name=room_name,
                room_sid=room_sid,
            )

        elif event_type == "track_published":
            track_info = payload.get("track", {})
            await SipEventHandler.on_track_published(
                room_name=room_name,
                participant_identity=participant_identity,
                track_sid=track_info.get("sid", ""),
                track_type=track_info.get("type", ""),
            )

        else:
            logger.debug("[SipWebhook] unhandled event type: %s", event_type)

    except Exception as exc:
        logger.exception("[SipWebhook] error processing event=%s: %s", event_type, exc)
        _metrics["calls_failed"] += 1
        # Return 200 so LiveKit considers the webhook delivered (no retry loop)

    return {"status": "ok", "event": event_type, "timestamp": time.time()}


# ══════════════════════════════════════════════════════════════════════════════
# Health & Status Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@sip_router.get("/health")
async def sip_health():
    """SIP subsystem health check."""
    kafka_active = False
    try:
        from ..kafka.producer import get_producer
        producer = get_producer()
        kafka_active = producer.is_kafka_active
    except Exception:
        pass

    return {
        "status": "ok" if ENABLE_SIP else "disabled",
        "sip_enabled": ENABLE_SIP,
        "active_sip_sessions": sip_session_mgr.active_count,
        "total_sip_sessions": sip_session_mgr.total_count,
        "kafka_active": kafka_active,
        "rate_limiter": {
            "current": _rate_limiter.current_count,
            "max": SIP_RATE_LIMIT_MAX,
            "window_sec": SIP_RATE_LIMIT_WINDOW,
        },
        "security": {
            "enforce_signature": SIP_ENFORCE_SIGNATURE,
            "caller_allowlist_enabled": len(SIP_ALLOWED_CALLERS) > 0,
            "allowed_callers_count": len(SIP_ALLOWED_CALLERS),
        },
        "timestamp": time.time(),
    }


@sip_router.get("/sessions")
async def sip_sessions():
    """List all tracked SIP sessions (active and recent)."""
    return {
        "sessions": sip_session_mgr.to_dict_list(),
        "active_count": sip_session_mgr.active_count,
        "total_count": sip_session_mgr.total_count,
    }


@sip_router.get("/session/{session_id}")
async def sip_session_detail(session_id: str):
    """Lookup a specific SIP session by session_id."""
    sess = sip_session_mgr.get_by_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="SIP session not found")
    return {
        "sip_call_id": sess.sip_call_id,
        "session_id": sess.session_id,
        "room_id": sess.room_id,
        "state": sess.state.value,
        "caller_number": sess.caller_number,
        "created_at": sess.created_at,
        "updated_at": sess.updated_at,
        "duration_sec": time.time() - sess.created_at,
        "participant_id": sess.livekit_participant_id,
    }


@sip_router.get("/metrics")
async def sip_metrics():
    """SIP-specific operational metrics."""
    active_sessions = sip_session_mgr.get_all_active()
    states = {}
    for sess in sip_session_mgr.to_dict_list():
        state = sess["state"]
        states[state] = states.get(state, 0) + 1

    return {
        "counters": _metrics.copy(),
        "sessions_by_state": states,
        "active_count": sip_session_mgr.active_count,
        "total_count": sip_session_mgr.total_count,
        "rate_limiter_usage": {
            "current": _rate_limiter.current_count,
            "max": SIP_RATE_LIMIT_MAX,
        },
        "timestamp": time.time(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Webhook Signature Validation
# ══════════════════════════════════════════════════════════════════════════════

def _validate_webhook_signature(
    body: bytes,
    auth_header: str,
    secret: str,
) -> bool:
    """
    Validate LiveKit webhook HMAC-SHA256 signature.

    LiveKit signs webhook payloads with:
        Authorization: Bearer <base64(HMAC-SHA256(secret, body))>

    Returns True if signature is valid, False otherwise.
    """
    if not auth_header:
        return False

    try:
        # Extract token from "Bearer <token>"
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False

        received_sig = base64.b64decode(parts[1])
        expected_sig = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()

        return hmac.compare_digest(received_sig, expected_sig)

    except Exception:
        return False
