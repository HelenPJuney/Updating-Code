# [ START: CALLER OR AGENT INTERACTION ]
#       |
#       |--- (A) GET  /livekit/caller-token (New Caller)
#       |--- (B) POST /livekit/agent-status (Update Helen)
#       |--- (C) POST /livekit/accept-call   (Helen Picks)
#       |--- (D) POST /tts/speak             (Audio Gen)
#       v
# +------------------------------------------+
# | CALLER ENTRY: caller_token()             |
# | * Generate room/session IDs              |
# | * Check agent_status (ringing vs queued) |
# +------------------------------------------+
#       |
#       |----> [ State: ringing ]           |----> [ State: queued ]
#       |      _ringing_loop()              |      _countdown_loop()
#       |      * Wait 30s for accept        |      * Send wait time via TTS
#       v                                   v
# +------------------------------------------+
# | AGENT ACTIONS                            |
# | * update_agent_status(): Set availability|
# | * decline_call(): Move caller to queue   |
# | * accept_call(): Join caller's room      |
# +------------------------------------------+
#       |
#       |----> [ Accept Call Logic ]
#       |      * Cancel countdown task
#       |      * Remove participant "piper-tts"
#       |      * Issue agent token for room
#       v
# +------------------------------------------+
# | TTS SUBSYSTEM                            |
# | * tts_speak(): Handle /tts/speak POST    |
# | * _run_piper(): Execute external engine   |
# | * _raw_pcm_to_wav(): Format audio header |
# +------------------------------------------+
#       |
#       |----> _send_tts_data_message()
#       |      * Reliable Data Channel JSON
#       v
# [ END: AUDIO PLAYED IN CALLER UI ]

import asyncio
import logging
import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .token_service import generate_token, LIVEKIT_URL

logger = logging.getLogger("callcenter.receiver")

receiver_router = APIRouter(tags=["receiver"])
tts_router = APIRouter(prefix="/tts", tags=["tts"])

# ── Queue state (in-memory) ──────────────────────────────────────────────────
# Each entry: {"session_id": str, "room_id": str, "joined_at": float, "countdown_task": Task|None, "caller_id": str, "state": str}
_caller_queue: deque = deque()
_WAIT_PER_CALLER_SEC = 120   # estimated seconds per call
_COUNTDOWN_INTERVAL  = 60    # announce every 60 seconds (1 minute configurable)

_agent_status = "offline"  # 'offline', 'available', 'busy'
_custom_wait_message = ""

HELEN_ROOM = "helen-room"

# ── TTS — Piper & Logging ────────────────────────────────────────────────────
_tts_cache = {}

# ── Piper config ─────────────────────────────────────────────────────────────
_PIPER_EXE   = os.getenv("PIPER_EXECUTABLE", "piper/piper.exe")
_PIPER_MODEL = os.getenv("PIPER_MODEL",      "piper/models/en_US-ryan-high.onnx")

_VOICE_MODELS = {
    "en_US-ryan-high":     "piper/models/en_US-ryan-high.onnx",
    "en_US-lessac-medium": "piper/models/en_US-lessac-medium.onnx",
    "en_US-ryan-medium":   "piper/models/en_US-ryan-medium.onnx",
}

class AgentStatusRequest(BaseModel):
    status: str
    custom_message: Optional[str] = None

@receiver_router.post("/livekit/agent-status")
async def update_agent_status(req: AgentStatusRequest):
    logger.debug("Executing update_agent_status")
    global _agent_status, _custom_wait_message
    if req.status in ["offline", "available", "busy"]:
        _agent_status = req.status
    if req.custom_message is not None:
        _custom_wait_message = req.custom_message
        if req.custom_message.strip():
            # Trigger immediate announcement to all queued callers
            for e in list(_caller_queue):
                if e.get("state") == "queued":
                    asyncio.create_task(_play_immediate_custom_announcement(e["room_id"], e["session_id"], req.custom_message))

    return {"status": _agent_status, "custom_message": _custom_wait_message}

async def _play_immediate_custom_announcement(room_name: str, session_id: str, message: str):
    """Fires off Piper TTS instantly using reliable data channel so receiver doesn't have to wait"""
    logger.debug("Executing _play_immediate_custom_announcement")
    await _send_tts_data_message(room_name, session_id, message)

@receiver_router.get("/livekit/agent-status")
async def get_agent_status():
    logger.debug("Executing get_agent_status")
    return {"status": _agent_status, "custom_message": _custom_wait_message}

@receiver_router.post("/livekit/decline-call/{session_id}")
async def decline_call(session_id: str):
    logger.debug("Executing decline_call")
    global _caller_queue
    for e in _caller_queue:
        if e["session_id"] == session_id and e.get("state") == "ringing":
            if e.get("countdown_task") and not e["countdown_task"].done():
                e["countdown_task"].cancel()
            e["state"] = "queued"
            position = len([x for x in _caller_queue if x.get("state") == "queued"])
            wait_sec = position * _WAIT_PER_CALLER_SEC
            task = asyncio.create_task(_countdown_loop(session_id, e["room_id"], wait_sec))
            e["countdown_task"] = task
            return {"status": "declined"}
    raise HTTPException(status_code=404, detail="Ringing session not found")

# ═══════════════════════════════════════════════════════════════════════════════
# Receiver token — Helen joins fixed room
# ═══════════════════════════════════════════════════════════════════════════════

@receiver_router.get("/livekit/receiver-token")
async def receiver_token(
    identity: str = Query("helen-receiver"),
    name: str = Query("Helen"),
):
    """Token for the receiver (Helen). Always joins HELEN_ROOM."""
    logger.debug("Executing receiver_token")
    try:
        token = generate_token(
            room_name    = HELEN_ROOM,
            identity     = identity,
            name         = name,
            can_publish  = True,
            can_subscribe= True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token generation failed: {e}")

    logger.info("receiver_token: identity=%s room=%s", identity, HELEN_ROOM)
    return {
        "token":       token,
        "url":         LIVEKIT_URL,
        "livekit_url": LIVEKIT_URL,
        "room":        HELEN_ROOM,
        "identity":    identity,
        "name":        name,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Caller token — joins same fixed room, gets queued
# ═══════════════════════════════════════════════════════════════════════════════

@receiver_router.get("/livekit/caller-token")
async def caller_token(
    caller_id: str = Query(default_factory=lambda: f"caller-{uuid.uuid4().hex[:8]}"),
    department: str = Query(""),
    urgency: int = Query(3),
):
    """
    Token for a caller. Joins unique room.
    Returns queue position and estimated wait time.
    Optionally accepts department + urgency from IVR routing.
    """
    logger.debug("Executing caller_token")
    session_id = str(uuid.uuid4())
    room_id = f"call-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    identity   = f"{caller_id}-{uuid.uuid4().hex[:6]}"

    try:
        token = generate_token(
            room_name    = room_id,
            identity     = identity,
            name         = f"Caller ({caller_id[:12]})",
            can_publish  = True,
            can_subscribe= True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token generation failed: {e}")

    is_ringing = (_agent_status == "available")

    entry = {
        "session_id": session_id,
        "room_id": room_id,
        "caller_id": caller_id,
        "joined_at": time.time(),
        "countdown_task": None,
        "state": "ringing" if is_ringing else "queued",
        "department": department,
        "urgency": min(max(urgency, 1), 5),
    }

    _caller_queue.append(entry)

    if is_ringing:
        task = asyncio.create_task(_ringing_loop(session_id, room_id, caller_id))
        entry["countdown_task"] = task
        position = 0
        wait_sec = 0
        wait_message = "Waiting for Helen to accept."
    else:
        position = len([x for x in _caller_queue if x.get("state") == "queued"])
        wait_sec = position * _WAIT_PER_CALLER_SEC
        task = asyncio.create_task(_countdown_loop(session_id, room_id, wait_sec))
        entry["countdown_task"] = task
        wait_message = f"Your wait time is roughly {wait_sec // 60} minutes."

    logger.info("caller_token: identity=%s room=%s state=%s", identity, room_id, entry["state"])
    return {
        "token":          token,
        "url":            LIVEKIT_URL,
        "livekit_url":    LIVEKIT_URL,
        "room":           room_id,
        "session_id":     session_id,
        "identity":       identity,
        "queue_position": position,
        "wait_seconds":   wait_sec,
        "wait_message":   wait_message
    }


async def _ringing_loop(session_id: str, room_name: str, caller_id: str):
    logger.debug("Executing _ringing_loop")
    for _ in range(30):
        await asyncio.sleep(1)
        still_ringing = any(e["session_id"] == session_id and e.get("state") == "ringing" for e in _caller_queue)
        if not still_ringing:
            return
        
    for e in _caller_queue:
        if e["session_id"] == session_id and e.get("state") == "ringing":
            e["state"] = "queued"
            position = len([x for x in _caller_queue if x.get("state") == "queued"])
            wait_sec = position * _WAIT_PER_CALLER_SEC
            task = asyncio.create_task(_countdown_loop(session_id, room_name, wait_sec))
            e["countdown_task"] = task
            break

async def _countdown_loop(session_id: str, room_name: str, initial_wait: int):
    """
    Every N seconds, play a Piper TTS announcement into the specific room
    telling the waiting caller their remaining wait time.
    """
    # Wait a moment for the client to actually join the room and mount listeners
    logger.debug("Executing _countdown_loop")
    await asyncio.sleep(2.5)

    model_path = _VOICE_MODELS.get("en_US-ryan-high", _PIPER_MODEL)

    def get_position_and_wait():
        logger.debug("Executing get_position_and_wait")
        queued = [x for x in _caller_queue if x.get("state") == "queued"]
        for i, q in enumerate(queued):
            if q["session_id"] == session_id:
                return i + 1, (i + 1) * _WAIT_PER_CALLER_SEC
        return 0, 0

    pos, wait = get_position_and_wait()
    mins = wait // 60
    if _custom_wait_message:
        msg = f"{_custom_wait_message}"
    else:
        msg = f"Our agents are currently busy. Your wait time is roughly {'less than a' if mins == 0 else mins} minute{'s' if mins > 1 else ''}."
    
    await _send_tts_data_message(room_name, session_id, msg)

    while True:
        try:
            await asyncio.sleep(_COUNTDOWN_INTERVAL)
        except asyncio.CancelledError:
            break
        
        still_waiting = any(e["session_id"] == session_id for e in _caller_queue)
        if not still_waiting:
            break

        pos, wait = get_position_and_wait()
        if pos == 0:
            break

        mins = wait // 60
        if _custom_wait_message:
            msg = f"Please hold. {_custom_wait_message}"
        else:
            msg = f"Please wait, you are number {pos} in the queue. Your estimated wait time is {mins} minutes."

        await _send_tts_data_message(room_name, session_id, msg)


async def _send_tts_data_message(room_name: str, session_id: str, message: str):
    """
    Sends a reliable LiveKit Data Channel message to the caller's isolated room.
    The frontend CallInterface listens for 'dataReceived', fetches /tts/speak, and plays the audio.
    This bypasses Python SDK WebRTC ICE/SDP entirely — no room.connect() needed.
    """
    logger.debug("Executing _send_tts_data_message")
    try:
        from livekit.api import LiveKitAPI, SendDataRequest, CreateRoomRequest
        import json

        api_key    = os.getenv("LIVEKIT_API_KEY",    "devkey")
        api_secret = os.getenv("LIVEKIT_API_SECRET", "devsecret")
        lk_url     = LIVEKIT_URL.replace("wss://", "https://")

        payload = json.dumps({"action": "play_tts", "text": message}).encode("utf-8")

        req = SendDataRequest(
            room=room_name,
            data=payload,
            kind=1,   # 1 = RELIABLE
            topic="tts",
        )
        async with LiveKitAPI(lk_url, api_key, api_secret) as api:
            await api.room.send_data(req)
        logger.info("TTS data sent to room %s: %s", room_name, message[:60])
    except Exception as e:
        logger.warning("_send_tts_data_message failed: %s", e)


@receiver_router.delete("/livekit/caller-queue/{session_id}")
async def remove_from_queue(session_id: str):
    """Remove caller from queue when they disconnect — cancels their countdown."""
    logger.debug("Executing remove_from_queue")
    global _caller_queue
    before = len(_caller_queue)
    new_queue = deque()
    for e in _caller_queue:
        if e["session_id"] == session_id:
            # Cancel countdown task
            if e.get("countdown_task") and not e["countdown_task"].done():
                e["countdown_task"].cancel()
        else:
            new_queue.append(e)
    _caller_queue = new_queue
    return {"removed": before - len(_caller_queue), "queue_depth": len(_caller_queue)}


@receiver_router.get("/livekit/queue-info")
async def queue_info():
    """Current queue depth and wait times."""
    logger.debug("Executing queue_info")
    queued = [e for e in _caller_queue if e.get("state") == "queued"]
    ringing = [e for e in _caller_queue if e.get("state") == "ringing"]

    # Sort by urgency (high urgency first) for priority queue
    queued_sorted = sorted(queued, key=lambda e: e.get("urgency", 3), reverse=True)

    return {
        "queue_depth": len(queued_sorted),
        "callers":     [
            {
                "session_id": e["session_id"],
                "room_id":    e["room_id"],
                "caller_id":  e["caller_id"],
                "wait_sec":   round(time.time() - e["joined_at"]),
                "position":   idx + 1,
                "department": e.get("department", ""),
                "urgency":    e.get("urgency", 3),
            }
            for idx, e in enumerate(queued_sorted)
        ],
        "ringing": [
            {
                "session_id": e["session_id"],
                "room_id":    e["room_id"],
                "caller_id":  e["caller_id"],
                "wait_sec":   round(time.time() - e["joined_at"]),
                "department": e.get("department", ""),
                "urgency":    e.get("urgency", 3),
            }
            for e in ringing
        ],
        "wait_per_caller_sec": _WAIT_PER_CALLER_SEC,
    }


@receiver_router.post("/livekit/accept-call/{session_id}")
async def accept_call(
    session_id: str,
    identity: str = Query("helen-receiver"),
    name: str = Query("Helen"),
):
    """
    Agent accepts a specific caller.
    1. Finds caller in queue
    2. Cancels TTS countdown and pops from queue
    3. Issues agent a token for the caller's unique room
    4. Kicks TTS from room if playing
    """
    logger.debug("Executing accept_call")
    global _caller_queue
    target_entry = None
    new_queue = deque()

    for e in _caller_queue:
        if e["session_id"] == session_id:
            target_entry = e
            if e.get("countdown_task") and not e["countdown_task"].done():
                e["countdown_task"].cancel()
        else:
            new_queue.append(e)

    _caller_queue = new_queue

    if not target_entry:
        raise HTTPException(status_code=404, detail="Session not found in queue")

    room_id = target_entry["room_id"]

    try:
        api_key = os.getenv("LIVEKIT_API_KEY", "devkey")
        api_secret = os.getenv("LIVEKIT_API_SECRET", "devsecret")
        lk_url = LIVEKIT_URL.replace("wss://", "https://")
        from livekit.api import LiveKitAPI
        try:
            async with LiveKitAPI(lk_url, api_key, api_secret) as api:
                from livekit.api import ListParticipantsRequest
                participants = await api.room.list_participants(ListParticipantsRequest(room=room_id))
                for p in participants.participants:
                    if p.identity.startswith("piper-tts"):
                        # In some SDK versions this is positional, in others it's a request object.
                        # We use the internal service method to be safe.
                        try:
                            await api.room.remove_participant(room=room_id, identity=p.identity)
                        except Exception:
                            # Fallback to positional if kwarg fails
                            await api.room.remove_participant(room_id, p.identity)
                        logger.info("Kicked TTS agent %s from room %s", p.identity, room_id)
        except Exception as api_exc:
            logger.warning("Failed to kick TTS agent: %s", api_exc)
    except Exception as imp_exc:
        logger.warning("api import failed: %s", imp_exc)

    try:
        token = generate_token(
            room_name    = room_id,
            identity     = identity,
            name         = name,
            can_publish  = True,
            can_subscribe= True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token generation failed: {e}")

    return {
        "status": "accepted",
        "token": token,
        "room": room_id,
        "url": LIVEKIT_URL,
        "session_id": session_id
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy join-room alias
# ═══════════════════════════════════════════════════════════════════════════════

@receiver_router.get("/livekit/join-room")
async def join_room(
    room: str = Query("helen-room"),
    identity: str = Query("helen-receiver"),
    name: str = Query("Helen"),
):
    logger.debug("Executing join_room")
    try:
        token = generate_token(
            room_name    = room,
            identity     = identity,
            name         = name,
            can_publish  = True,
            can_subscribe= True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"token": token, "url": LIVEKIT_URL, "livekit_url": LIVEKIT_URL, "room": room}


# ═══════════════════════════════════════════════════════════════════════════════
# TTS — Piper
# ═══════════════════════════════════════════════════════════════════════════════

class TtsSpeakRequest(BaseModel):
    text: str
    voice: str = "en_US-ryan-high"
    room_id: Optional[str] = None
    session_id: Optional[str] = None


@tts_router.post("/speak")
async def tts_speak(body: TtsSpeakRequest):
    logger.debug("Executing tts_speak")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    model_path = _VOICE_MODELS.get(body.voice, _PIPER_MODEL)
    
    # Use cached audio if available (text + voice fingerprint)
    cache_key = f"{body.voice}:{text}"
    if cache_key in _tts_cache:
        wav_bytes = _tts_cache[cache_key]
    else:
        wav_bytes  = await _run_piper(text, model_path)
        if wav_bytes:
            _tts_cache[cache_key] = wav_bytes

    if wav_bytes is None:
        raise HTTPException(
            status_code=503,
            detail=f"Piper TTS not available. Expected exe at: {_PIPER_EXE}"
        )

    from fastapi.responses import Response
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"X-Chars": str(len(text)), "X-Voice": body.voice},
    )


async def _run_piper(text: str, model_path: str) -> Optional[bytes]:
    logger.debug("Executing _run_piper")
    exe   = Path(_PIPER_EXE)
    model = Path(model_path)

    if not exe.exists():
        logger.warning("Piper exe not found: %s", exe)
        return None
    if not model.exists():
        logger.warning("Piper model not found: %s", model)
        return None

    try:
        espeak_data_path = str(Path("piper/espeak-ng-data").absolute())
        # Force raw output so we can wrap it in a perfect WAV header manually.
        # This prevents "fata hua" (distorted) sound caused by header/bitrate mismatches.
        proc = await asyncio.create_subprocess_exec(
            str(exe.absolute()), "--model", str(model.absolute()), 
            "--output_raw", 
            "--espeak_data", espeak_data_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=text.encode("utf-8")), timeout=45.0
        )
        if proc.returncode != 0:
            logger.error("Piper error: %s", stderr.decode())
            return None
        
        # Wrap raw PCM into a valid WAV header
        return _raw_pcm_to_wav(stdout, 22050)
    except asyncio.TimeoutError:
        logger.error("Piper TTS timed out")
        return None
    except Exception as e:
        logger.error("Piper TTS failed: %s", e)
        return None


def _raw_pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 22050) -> bytes:
    logger.debug("Executing _raw_pcm_to_wav")
    import wave, io
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()
