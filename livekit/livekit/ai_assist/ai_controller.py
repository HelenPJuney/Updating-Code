"""
ai_assist/ai_controller.py
────────────────────────────────────────────────────────────────────────────
AI assist router — manual join + LiveKit webhook auto-join.

FIX #10: source is detected from room metadata and passed to join_room()
         so AI join manager can log and trace per source.

FIX #10: AI joins ONLY when:
         - a human agent/human- participant joins, OR
         - room metadata explicitly sets ai_auto_join=true

FIX #11: Structured logging with room_id, source, participant identity.

FIX #4:  source is forwarded through to join_room() for traceability.
"""

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel

from livekit.ai_assist.ai_join_manager import ai_join_manager

logger = logging.getLogger("callcenter.ai_assist.controller")

ai_assist_router = APIRouter(prefix="/ai", tags=["ai_assist"])


# ─── Manual join request ──────────────────────────────────────────────────────

class AIJoinRequest(BaseModel):
    room_id: str
    mode:    str = "assist_mode"
    lang:    str = "en"
    source:  str = "browser"   # "browser" | "sip" — for tracing


@ai_assist_router.post("/join")
async def ai_join(req: AIJoinRequest) -> dict:
    """
    Manually trigger AI to join an ongoing call.
    Accepts source so callers can distinguish browser vs SIP rooms.
    """
    _start = time.perf_counter()
    logger.info(
        "[ai_join] START  room=%.8s  mode=%s  lang=%s  source=%s",
        req.room_id, req.mode, req.lang, req.source,
    )
    try:
        await ai_join_manager.join_room(
            room_id = req.room_id,
            mode    = req.mode,
            lang    = req.lang,
            source  = req.source,   # FIX #10: pass source through
        )
        logger.info(
            "[ai_join] END  room=%.8s  elapsed=%.4fs",
            req.room_id, time.perf_counter() - _start,
        )
        return {
            "status":  "success",
            "message": f"AI joining {req.room_id} in {req.mode} (lang={req.lang}, source={req.source})",
        }
    except Exception as exc:
        logger.error("[ai_join] ERROR  room=%.8s  error=%s", req.room_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─── LiveKit webhook auto-join ────────────────────────────────────────────────

@ai_assist_router.post("/webhook")
async def livekit_webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
    """
    LiveKit Webhook — auto-join AI when a human agent joins a room.

    FIX #10: source is extracted from room metadata (set by browser/router.py
             or SIP handler) so join_room() knows the call origin.
    FIX #10: joins ONLY when a human-* or agent-* participant joins
             (not on every PARTICIPANT_JOINED event).
    FIX #11: structured logging.
    """
    _start = time.perf_counter()
    logger.info("[livekit_webhook] START")

    try:
        from livekit.protocol import webhook
        from livekit.token_service import LIVEKIT_API_KEY, LIVEKIT_API_SECRET

        body        = await request.body()
        auth_header = request.headers.get("Authorization", "")

        try:
            receiver = webhook.WebhookReceiver(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            event    = receiver.receive(body.decode("utf-8"), auth_header)
        except Exception as verify_exc:
            # FIX #11: reject invalid signatures explicitly (not silently)
            logger.warning("[livekit_webhook] signature verification failed: %s", verify_exc)
            raise HTTPException(status_code=401, detail="Invalid LiveKit webhook signature")

        if event.event != webhook.Event.PARTICIPANT_JOINED:
            return {"status": "ok", "action": "ignored"}

        room_id           = getattr(event.room,        "name",     "")
        participant_ident = getattr(event.participant,  "identity", "")
        room_metadata_raw = getattr(event.room,        "metadata", "") or ""

        # FIX #10: parse room metadata for source + ai config
        meta: dict = {}
        if room_metadata_raw:
            try:
                meta = json.loads(room_metadata_raw)
            except Exception:
                logger.debug("[livekit_webhook] room metadata is not JSON: %.60s", room_metadata_raw)

        source     = meta.get("source", "browser")      # "browser" | "sip"
        mode       = meta.get("ai_mode",    "assist_mode")
        lang       = meta.get("ai_lang",    "en")
        auto_join  = meta.get("ai_auto_join", False)    # explicit override

        # FIX #10: only join when a HUMAN participant joins
        is_human = (
            "agent-" in participant_ident
            or "human-" in participant_ident
            or auto_join
        )

        if not is_human:
            logger.debug(
                "[livekit_webhook] participant=%s is not a human agent — skipping AI join",
                participant_ident,
            )
            return {"status": "ok", "action": "no_join"}

        logger.info(
            "[livekit_webhook] human joined  room=%.8s  participant=%s  source=%s  mode=%s  lang=%s",
            room_id, participant_ident, source, mode, lang,
        )

        # Non-blocking — debounce guard inside join_room prevents duplicates
        background_tasks.add_task(
            ai_join_manager.join_room,
            room_id,
            mode,
            lang,
            source,   # FIX #10: pass source so debounce/logging works correctly
        )

        logger.info(
            "[livekit_webhook] END  elapsed=%.4fs", time.perf_counter() - _start,
        )
        return {"status": "ok", "action": "ai_join_scheduled"}

    except HTTPException:
        raise
    except Exception:
        logger.exception("[livekit_webhook] UNHANDLED ERROR")
        # Return ok to LiveKit so it doesn't retry — internal errors shouldn't
        # cause webhook flood
        return {"status": "error", "message": "internal error — check server logs"}
