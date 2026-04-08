# [ START: POST /browser/call/start ]
#       |
#       v
# +------------------------------------------+
# | * browser_call_start(body)               |
# |   * Generate session_id & room_id        |
# +------------------------------------------+
#       |
#       |----> * CallRequest() (Unified Schema)
#       |        *  caller_id set
#       |        *  llm/voice defaults
#       |        *  Forward metadata
#       v
# +------------------------------------------+
# | 2. Routing Engine (Singleton)            |
# +------------------------------------------+
#       |
#       |----> * get_routing_engine()
#       |----> * routing_engine.route(req)
#       |----> * event_hub.publish_routing_decision()
#       v
# +------------------------------------------+
# | 3. Offline / Overload Check              |
# +------------------------------------------+
#       |
#       |----> * offline_handler.check_status()
#       |           |
#       |      [ Status: OFFLINE ] --------> * offline_handler.handle()
#       |           |                        * RETURN: status="fallback"
#       |           |
#       |      [ Status: OVERLOADED ] -----> * offline_handler.handle() (Priority Bump)
#       v
# +------------------------------------------+
# | 4. Submit to Kafka                       |
# +------------------------------------------+
#       |
#       |----> * get_producer().submit_call_request()
#       |           |
#       |      [ Kafka Success ] ----------> status="queued"
#       |           |
#       |      [ Kafka Failed ] -----------> status="direct"
#       |                                    * _direct_ai_spawn()
#       v
# +------------------------------------------+
# | 5. Finalize Browser Access               |
# +------------------------------------------+
#       |
#       |----> * event_hub.publish("call_queued")
#       |----> * _issue_token()
#       |           |
#       |           * browser-{id}-{uuid} (Unique)
#       v
# +------------------------------------------+
# | RETURN BrowserCallResponse:              |
# | { session_id, room_id, token,            |
# |   livekit_url, status, queue_pos }       |
# +------------------------------------------+
#       |
#       [ END ]

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from ..kafka.producer import get_producer
from ..kafka.schemas import CallRequest
from ..routing.singleton import get_routing_engine
from ..offline.handler import offline_handler, OfflineStatus
from ..token_service import generate_token, LIVEKIT_URL
from ..websocket import event_hub

logger = logging.getLogger("callcenter.browser.router")

browser_router = APIRouter(prefix="/browser", tags=["browser"])

# ─── Request / Response schemas ───────────────────────────────────────────────

class BrowserCallRequest(BaseModel):
    """
    JSON body for POST /browser/call/start.
    """
    caller_id: str = Field(default_factory=lambda: f"browser-{uuid.uuid4().hex[:8]}")
    lang:      str = "en"
    priority:  int = 0
    source:    str = "browser"
    metadata:  Dict[str, Any] = Field(default_factory=dict)


class BrowserCallResponse(BaseModel):
    session_id:      str
    room_id:         str
    token:           str
    livekit_url:     str
    status:          str             # "queued" | "direct" | "fallback"
    source:          str = "browser"
    queue_position:  Optional[int] = None
    fallback_action: Optional[str] = None
    message:         str = ""


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@browser_router.post("/call/start", response_model=BrowserCallResponse)
async def browser_call_start(body: BrowserCallRequest) -> BrowserCallResponse:
 
    _start = time.perf_counter()
    logger.info(
        "[browser/call/start] START  caller_id=%.16s  lang=%s  priority=%d",
        body.caller_id, body.lang, body.priority,
    )

    try:
        session_id = str(uuid.uuid4())
        room_id    = str(uuid.uuid4())

        # ── 1. Build unified CallRequest ────────────────────────────────────────
    
        req = CallRequest(
            session_id  = session_id,
            room_id     = room_id,
            lang        = body.lang,
            priority    = body.priority,
            source      = body.source,
            caller_id   = body.caller_id,
            caller_number = "",          # SIP field — intentionally blank for browser calls
            llm         = "gemini",      # guaranteed default
            voice       = "",            # guaranteed default
            agent_name  = "AI Assistant",# guaranteed default
            metadata    = body.metadata, # FIX #7 — was dropped before
        )

        # ── 2. Routing Engine — singleton, same rules as SIP ────────────────────

        try:
            routing_engine = get_routing_engine()
            decision = await routing_engine.route(req)
            decision.apply(req)

            # After apply(), re-apply guaranteed defaults in case ai_config cleared them
            req.llm        = req.llm        or "gemini"
            req.voice      = req.voice      or ""
            req.agent_name = req.agent_name or "AI Assistant"

            logger.info(
                "[browser/call/start] routing rule=%s queue=%s  session=%.8s",
                decision.rule_name, req.queue_name, session_id,
            )
            await event_hub.publish_routing_decision(session_id, decision.rule_name, req.queue_name)
        except Exception as routing_exc:
            logger.warning("[browser/call/start] routing failed, using defaults: %s", routing_exc)

        # ── 3. Offline / overload check ─────────────────────────────────────────
        system_status = await offline_handler.check_status()

        if system_status == OfflineStatus.OFFLINE:
            fallback = await offline_handler.handle(req, system_status)
            token = _issue_token(room_id, body.caller_id)
            logger.info(
                "[browser/call/start] OFFLINE fallback=%s  session=%.8s",
                fallback.action, session_id,
            )
            return BrowserCallResponse(
                session_id      = session_id,
                room_id         = room_id,
                token           = token,
                livekit_url     = LIVEKIT_URL,
                status          = "fallback",
                source          = body.source,
                fallback_action = fallback.action,
                message         = fallback.message,
            )

        if system_status == OfflineStatus.OVERLOADED:
            await offline_handler.handle(req, system_status)  # applies priority bump

        # ── 4. Submit to Kafka (or direct AI spawn fallback) ────────────────────
        producer   = get_producer()
        result     = await producer.submit_call_request(req)
        call_status: str
        queue_pos: Optional[int]

        if result is not None:
            call_status = "queued"
            queue_pos   = result  # 0 placeholder; real position via DataChannel
        else:
            call_status = "direct"
            queue_pos   = None
            await _direct_ai_spawn(req)

        # ── 5. Publish call_queued event to WebSocket hub ────────────────────────
        await event_hub.publish({
            "type":       "call_queued",
            "session_id": session_id,
            "room_id":    room_id,
            "source":     body.source,
            "caller_id":  body.caller_id,
            "lang":       body.lang,
            "queue_name": req.queue_name,
            "ts":         time.time(),
        })

        # ── 6. Issue LiveKit token ────────────────────────────────────────────────
        token = _issue_token(room_id, body.caller_id)

        elapsed = time.perf_counter() - _start
        logger.info(
            "[browser/call/start] END  session=%.8s  status=%s  elapsed=%.4fs",
            session_id, call_status, elapsed,
        )
        return BrowserCallResponse(
            session_id     = session_id,
            room_id        = room_id,
            token          = token,
            livekit_url    = LIVEKIT_URL,
            status         = call_status,
            source         = body.source,
            queue_position = queue_pos,
            message        = f"Call {call_status} — lang={body.lang}",
        )

    except HTTPException:
        raise
    except Exception:
        logger.exception("[browser/call/start] UNHANDLED ERROR  caller_id=%.16s", body.caller_id)
        raise HTTPException(status_code=500, detail="Failed to start browser call")


@browser_router.get("/call/{session_id}/status")
async def browser_call_status(session_id: str):
   
    logger.debug("[browser/call/status] session=%.8s", session_id)
    from ..kafka.scheduler import get_cached_lag
    return {
        "session_id":  session_id,
        "queue_depth": get_cached_lag(),
        "ts":          time.time(),
    }


# ─── Private helpers ──────────────────────────────────────────────────────────

def _issue_token(room_id: str, caller_id: str) -> str:
   
    identity = f"browser-{caller_id[:12]}-{uuid.uuid4().hex[:6]}"
    try:
        return generate_token(
            room_name    = room_id,
            identity     = identity,
            name         = f"Caller ({caller_id[:16]})",
            can_publish  = True,
            can_subscribe= True,
        )
    except Exception as exc:
        logger.error("[BrowserRouter] token generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Token generation failed")


async def _direct_ai_spawn(req: CallRequest) -> None:
   
    # Safe defaults — never pass empty strings that crash ai_worker_task
    llm        = req.llm        or "gemini"
    voice      = req.voice      or ""
    agent_name = req.agent_name or "AI Assistant"
    model_path = req.model_path or ""

    try:
        from ..ai_worker import ai_worker_task
        import asyncio
        asyncio.ensure_future(
            ai_worker_task(
                room_id    = req.room_id,
                session_id = req.session_id,
                lang       = req.lang,
                llm_key    = llm,
                voice_stem = voice,
                model_path = model_path,
                agent_name = agent_name,
            )
        )
        logger.info(
            "[BrowserRouter] direct AI spawn  session=%.8s  room=%.8s  llm=%s",
            req.session_id, req.room_id, llm,
        )
    except ImportError:
        logger.error("[BrowserRouter] ai_worker_task not importable — cannot spawn AI worker")
        raise HTTPException(status_code=503, detail="AI worker unavailable")
