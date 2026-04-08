# [ START: CALLER SPEAKS ]
#       |
#       v
# +------------------------------------------+
# | ivr_router -> /greeting (GET)            |
# | * Return pre-cached greeting WAV         |
# +------------------------------------------+
#       |
#       | (Caller provides issue)
#       v
# +------------------------------------------+
# | ivr_router -> /process (POST)            |
# | * Main brain of the IVR session          |
# +------------------------------------------+
#       |
#       |----> classify_intent(transcript)
#       |      * Determine Dept and Urgency
#       |
#       |----> synthesize(routing_msg)
#       |      * Prepare TTS "Routing to..."
#       v
# +------------------------------------------+
# | ivr_router -> /routing-audio (GET)       |
# | * Fetch specific department WAV          |
# +------------------------------------------+
#       |
#       |----> [ Helper Methods ]
#       |      * _get_whisper_model() (STT)
#       |      * /transcribe (Audio -> Text)
#       |      * /status (Health check)
#       v
# [ END: CALL ROUTED TO QUEUE ]

import asyncio
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import (
    GREETING_PHRASE, CONFIRMATION_PHRASE,
    SILENCE_THRESHOLD_SEC, VALID_DEPARTMENTS,
)
from .tts_engine import synthesize, pre_warm
from .intent_classifier import classify_intent

logger = logging.getLogger("ivr.agent")

ivr_router = APIRouter(prefix="/ivr", tags=["ivr"])

# ── State tracking for active IVR sessions ───────────────────────────────────
_active_sessions = {}


class IvrProcessRequest(BaseModel):
    """Request to process a caller's spoken text through the IVR pipeline."""
    session_id: str
    room_id: str
    transcript: str              # The caller's spoken words (from frontend STT or backend)
    caller_id: Optional[str] = None


class IvrProcessResponse(BaseModel):
    department: str
    urgency: int
    routing_message: str
    session_id: str


class IvrSessionState(BaseModel):
    session_id: str
    room_id: str
    state: str = "greeting"      # greeting | listening | confirming | classifying | routing | done
    transcript: str = ""
    department: Optional[str] = None
    urgency: int = 3
    started_at: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# IVR Process Endpoint — The main brain
# ═══════════════════════════════════════════════════════════════════════════════

@ivr_router.post("/process", response_model=IvrProcessResponse)
async def ivr_process(req: IvrProcessRequest):
    """
    Main IVR processing endpoint.
    
    The frontend captures audio via browser MediaRecorder, sends it to
    the backend for STT, then calls this endpoint with the transcript.
    
    This endpoint:
      1. Classifies intent via Gemini (with urgency/sentiment)
      2. Returns the department + urgency for routing
      3. The frontend/backend then routes the caller to the right queue
    """
    logger.debug("Executing ivr_process")
    transcript = req.transcript.strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="Empty transcript")

    logger.info("[IVR] Processing transcript for session %s: %s",
                req.session_id, transcript[:80])

    # ── Step 1: Classify with Gemini ─────────────────────────────────────────
    department, urgency = await classify_intent(transcript)

    logger.info("[IVR] Result: %s | Urgency: %d | Session: %s",
                department, urgency, req.session_id)

    # ── Step 2: Generate routing TTS message ─────────────────────────────────
    routing_msg = f"I am routing your call to the {department}."

    # Pre-synthesize the routing message so it's ready for playback
    await synthesize(routing_msg)

    return IvrProcessResponse(
        department=department,
        urgency=urgency,
        routing_message=routing_msg,
        session_id=req.session_id,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# IVR Greeting — Returns pre-cached greeting WAV
# ═══════════════════════════════════════════════════════════════════════════════

@ivr_router.get("/greeting")
async def ivr_greeting():
    """
    Returns the pre-synthesized greeting WAV.
    This is called once when the caller connects — instant playback, 0 CPU.
    """
    logger.debug("Executing ivr_greeting")
    wav = await synthesize(GREETING_PHRASE)
    if wav is None:
        raise HTTPException(status_code=503, detail="TTS engine unavailable")

    from fastapi.responses import Response
    return Response(content=wav, media_type="audio/wav")


@ivr_router.get("/confirmation-prompt")
async def ivr_confirmation():
    """Returns the pre-cached 'That's it sir?' WAV."""
    logger.debug("Executing ivr_confirmation")
    wav = await synthesize(CONFIRMATION_PHRASE)
    if wav is None:
        raise HTTPException(status_code=503, detail="TTS engine unavailable")

    from fastapi.responses import Response
    return Response(content=wav, media_type="audio/wav")


@ivr_router.get("/routing-audio/{department}")
async def ivr_routing_audio(department: str):
    """
    Synthesizes and returns the routing announcement WAV for a specific department.
    E.g. "I am routing your call to the Tech Department."
    """
    # Validate department
    logger.debug("Executing ivr_routing_audio")
    dept_clean = department.replace("-", " ").title()
    if dept_clean not in VALID_DEPARTMENTS:
        # Fuzzy match
        matched = None
        for valid in VALID_DEPARTMENTS:
            if department.lower() in valid.lower():
                matched = valid
                break
        if not matched:
            raise HTTPException(status_code=400, detail=f"Unknown department: {department}")
        dept_clean = matched

    msg = f"I am routing your call to the {dept_clean}."
    wav = await synthesize(msg)
    if wav is None:
        raise HTTPException(status_code=503, detail="TTS engine unavailable")

    from fastapi.responses import Response
    return Response(content=wav, media_type="audio/wav")


# ═══════════════════════════════════════════════════════════════════════════════
# STT Endpoint — faster-whisper transcription
# ═══════════════════════════════════════════════════════════════════════════════

_whisper_model = None


def _get_whisper_model():
    """Lazy-load faster-whisper model (tiny.en, int8, CPU)."""
    logger.debug("Executing _get_whisper_model")
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            from .config import WHISPER_MODEL_SIZE, WHISPER_COMPUTE_TYPE, WHISPER_DEVICE

            logger.info("Loading faster-whisper model: %s (%s, %s)",
                        WHISPER_MODEL_SIZE, WHISPER_COMPUTE_TYPE, WHISPER_DEVICE)

            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
            logger.info("faster-whisper loaded successfully")
        except ImportError:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            return None
        except Exception as e:
            logger.error("Failed to load faster-whisper: %s", e)
            return None
    return _whisper_model


@ivr_router.post("/transcribe")
async def ivr_transcribe():
    """
    Receives raw audio (WAV/WebM) from the frontend, transcribes via faster-whisper.
    Returns the transcript text.
    """
    logger.debug("Executing ivr_transcribe")
    from fastapi import Request
    from starlette.requests import Request as StarletteRequest
    import tempfile

    # This will be called with the audio file in the request body
    # For now, return a placeholder — the frontend handles STT via Web Speech API
    # as a more potato-friendly approach (offloads STT to browser)
    return {"info": "Use browser Web Speech API for transcription (potato-friendly)"}


# ═══════════════════════════════════════════════════════════════════════════════
# Health & Status
# ═══════════════════════════════════════════════════════════════════════════════

@ivr_router.get("/status")
async def ivr_status():
    """IVR system health check."""
    logger.debug("Executing ivr_status")
    from .config import GEMINI_API_KEY
    return {
        "status": "ok",
        "whisper_loaded": _whisper_model is not None,
        "gemini_configured": bool(GEMINI_API_KEY),
        "departments": VALID_DEPARTMENTS,
        "active_sessions": len(_active_sessions),
    }


@ivr_router.get("/departments")
async def list_departments():
    """Returns available departments for routing."""
    logger.debug("Executing list_departments")
    return {"departments": VALID_DEPARTMENTS}
