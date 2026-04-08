# [ START: API REQUEST ]
#       |
#       v
# +------------------------------------------+
# | <FastAPI> -> Depends(validate_api_key)   |
# | * Check X-API-Key & Rate Limits          |
# +------------------------------------------+
#       |
#       |--- [ Auth Fail ] ---> [ RETURN: 401/429 ]
#       |
#       |--- [ Auth Success ]
#       v
#       +-------------------------------------------------------+
#       |            SELECT ENDPOINT BY PATH                    |
#       +------------+-------------+--------------+-------------+
#                    |                            |
#       [ POST /call/start ]            [ POST /call/browser/start ]
#       (SIP / PSTN Path)               (WebRTC / Browser Path)
#                    |                            |
#       +----------------------------+  +----------------------------+
#       | * integration_service.     |  | * integration_service.     |
#       |   start_call()             |  |   start_browser_call()     |
#       |   * Trigger outbound SIP   |  |   * Generate WebRTC Token  |
#       +----------------------------+  +----------------------------+
#                    |                            |
#                    +-------------+--------------+
#                                  |
#                                  v
#                   +------------------------------+
#                   | Unified Response Shape:      |
#                   | {session_id, room_id, token, |
#                   |  livekit_url, status}        |
#                   +------------------------------+
#                                  |
#       +--------------------------+--------------------------+
#       |                                                     |
# [ GET /call/{id}/status ]                 [ POST /webhook/register ]
#       |                                                     |
# +----------------------------+                +----------------------------+
# | * integration_service.     |                | * integration_service.     |
# |   get_call_status()        |                |   register_webhook()       |
# |   * Fetch session data     |                |   * Forward Secret (FIX #9)|
# +----------------------------+                +----------------------------+
#       |                                                     |
# [ Check if Found? ]                                [ RETURN: Success JSON ]
#       |                                                     |
#       |--- [ NO  ] ---> raise 404 Error                     |
#       |--- [ YES ] ---> RETURN: Status JSON                 |
#       v                                                     v
#    [ END ]                                               [ END ]

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from livekit.integration.auth import validate_api_key
from livekit.integration.schemas import (
    CallStartRequest,
    CallStartResponse,
    BrowserCallStartRequest,
    CallStatusResponse,
    WebhookRegisterRequest,
    WebhookRegisterResponse,
)
from livekit.integration.service import integration_service

logger = logging.getLogger("callcenter.integration.router")

integration_router = APIRouter(tags=["integration"])


# ── POST /call/start  (SIP / PSTN) ───────────────────────────────────────────

@integration_router.post("/call/start", response_model=CallStartResponse)
async def start_call(
    request: CallStartRequest,
    client_id: str = Depends(validate_api_key),
) -> CallStartResponse:
    """
    Trigger an outbound SIP/PSTN call.
    FIX #12: Returns {session_id, room_id, token, livekit_url, status}.
    """
    _start = time.perf_counter()
    logger.info("[start_call] START  client=%s  phone=%.8s", client_id, request.phone_number)
    try:
        result = await integration_service.start_call(
            phone_number = request.phone_number,
            lang         = request.lang,
            source       = request.source,   # "external_sip"
            metadata     = request.metadata,
        )
        logger.info(
            "[start_call] END  session=%.8s  status=%s  elapsed=%.4fs",
            result["session_id"], result["status"], time.perf_counter() - _start,
        )
        return CallStartResponse(
            session_id  = result["session_id"],
            room_id     = result["room_id"],
            token       = result["token"],       # empty for SIP
            livekit_url = result["livekit_url"], # empty for SIP
            status      = result["status"],
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[start_call] ERROR  client=%s", client_id)
        raise HTTPException(status_code=500, detail="Failed to start SIP call")


# ── POST /call/browser/start  (WebRTC) ────────────────────────────────────────

@integration_router.post("/call/browser/start", response_model=CallStartResponse)
async def start_browser_call(
    request: BrowserCallStartRequest,
    client_id: str = Depends(validate_api_key),
) -> CallStartResponse:

    _start = time.perf_counter()
    caller = request.caller_id or client_id
    logger.info("[start_browser_call] START  client=%s  caller_id=%.16s", client_id, caller)
    try:
        result = await integration_service.start_browser_call(
            caller_id = caller,
            lang      = request.lang,
            source    = "external_browser",   # FIX #1: correct source
            priority  = request.priority,
            metadata  = {**request.metadata, "client_id": client_id},
        )
        logger.info(
            "[start_browser_call] END  session=%.8s  status=%s  elapsed=%.4fs",
            result["session_id"], result["status"], time.perf_counter() - _start,
        )
        return CallStartResponse(
            session_id  = result["session_id"],
            room_id     = result["room_id"],
            token       = result["token"],
            livekit_url = result["livekit_url"],
            status      = result["status"],
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[start_browser_call] ERROR  client=%s", client_id)
        raise HTTPException(status_code=500, detail="Failed to start browser call")


# ── GET /call/{session_id}/status ─────────────────────────────────────────────

@integration_router.get("/call/{session_id}/status", response_model=CallStatusResponse)
async def get_call_status(
    session_id: str,
    client_id: str = Depends(validate_api_key),
) -> CallStatusResponse:
    """Return current status for a call session."""
    _start = time.perf_counter()
    logger.info("[get_call_status] session=%.8s  client=%s", session_id, client_id)
    try:
        data = integration_service.get_call_status(session_id)
        if not data:
            raise HTTPException(status_code=404, detail=f"Call {session_id} not found")
        logger.info(
            "[get_call_status] status=%s  elapsed=%.4fs",
            data.get("status"), time.perf_counter() - _start,
        )
        return CallStatusResponse(**data)
    except HTTPException:
        raise
    except Exception:
        logger.exception("[get_call_status] ERROR  session=%.8s", session_id)
        raise HTTPException(status_code=500, detail="Error fetching call status")


# ── POST /webhook/register ────────────────────────────────────────────────────

@integration_router.post("/webhook/register", response_model=WebhookRegisterResponse)
async def register_webhook(
    request: WebhookRegisterRequest,
    client_id: str = Depends(validate_api_key),
) -> WebhookRegisterResponse:
  
    _start = time.perf_counter()
    logger.info("[register_webhook] client=%s  url=%.40s", client_id, request.url)
    try:
        integration_service.register_webhook(
            client_id = client_id,
            url       = request.url,
            events    = request.events,
            secret    = request.secret,   # FIX #9: pass secret through
        )
        logger.info(
            "[register_webhook] END  elapsed=%.4fs", time.perf_counter() - _start,
        )
        return WebhookRegisterResponse(
            status  = "success",
            message = f"Webhook registered for {client_id} ({len(request.events)} events)",
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("[register_webhook] ERROR  client=%s", client_id)
        raise HTTPException(status_code=500, detail="Failed to register webhook")
