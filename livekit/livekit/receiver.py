"""
receiver.py

Fixed room approach:
  - Receiver (Helen) always joins a fixed room: "helen-room"
  - Callers also join "helen-room" directly
  - Backend tracks caller queue: if Helen is busy, new callers get a TTS wait message
  - Queue endpoint returns position + estimated wait time

Endpoints:
  GET  /livekit/receiver-token          — token for Helen (fixed room)
  GET  /livekit/caller-token            — token for caller (fixed room, queued)
  GET  /livekit/queue-info              — current queue state
  POST /tts/speak                       — Piper TTS WAV response
"""

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

# ── Fixed shared room ────────────────────────────────────────────────────────
HELEN_ROOM = "helen-room"

# ── Queue state (in-memory) ──────────────────────────────────────────────────
# Each entry: {"session_id": str, "joined_at": float, "countdown_task": Task|None}
_caller_queue: deque = deque()
_WAIT_PER_CALLER_SEC = 120   # estimated seconds per call
_COUNTDOWN_INTERVAL  = 10    # announce every N seconds

# ── Piper config ─────────────────────────────────────────────────────────────
_PIPER_EXE   = os.getenv("PIPER_EXECUTABLE", "piper/piper.exe")
_PIPER_MODEL = os.getenv("PIPER_MODEL",      "piper/models/en_US-ryan-high.onnx")

_VOICE_MODELS = {
    "en_US-ryan-high":     "piper/models/en_US-ryan-high.onnx",
    "en_US-helen-medium":  "piper/models/en_US-helen-medium.onnx",
    "en_US-lessac-medium": "piper/models/en_US-lessac-medium.onnx",
    "en_US-ryan-medium":   "piper/models/en_US-ryan-medium.onnx",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Receiver token — Helen joins fixed room
# ═══════════════════════════════════════════════════════════════════════════════

@receiver_router.get("/livekit/receiver-token")
async def receiver_token(
    identity: str = Query("helen-receiver"),
    name: str = Query("Helen"),
):
    """Token for the receiver (Helen). Always joins HELEN_ROOM."""
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
):
    """
    Token for a caller. Joins HELEN_ROOM.
    Returns queue position and estimated wait time.
    """
    session_id = str(uuid.uuid4())
    identity   = f"caller-{caller_id[:12]}-{uuid.uuid4().hex[:6]}"

    try:
        token = generate_token(
            room_name    = HELEN_ROOM,
            identity     = identity,
            name         = f"Caller ({caller_id[:12]})",
            can_publish  = True,
            can_subscribe= True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token generation failed: {e}")

    # Add to queue (frontend browser handles TTS announcements, no server-side loop)
    entry = {"session_id": session_id, "joined_at": time.time(), "countdown_task": None}
    _caller_queue.append(entry)
    position = len(_caller_queue)
    wait_sec = (position - 1) * _WAIT_PER_CALLER_SEC

    logger.info("caller_token: identity=%s queue_pos=%d wait=%ds", identity, position, wait_sec)
    return {
        "token":          token,
        "url":            LIVEKIT_URL,
        "livekit_url":    LIVEKIT_URL,
        "room":           HELEN_ROOM,
        "session_id":     session_id,
        "identity":       identity,
        "queue_position": position,
        "wait_seconds":   wait_sec,
        "wait_message":   (
            "Connecting you to Helen now." if wait_sec == 0
            else f"Helen is busy on another call. Your wait time is {wait_sec} seconds."
        ),
    }


async def _countdown_loop(session_id: str, initial_wait: int):
    """
    Every 10 seconds, play a Piper TTS announcement into helen-room
    telling the waiting caller their remaining wait time.
    Stops when session is removed from queue or wait hits 0.
    """
    remaining = initial_wait
    model_path = _VOICE_MODELS.get("en_US-ryan-high", _PIPER_MODEL)

    # First announcement immediately
    msg = f"Helen is busy on another call. Your estimated wait time is {remaining} seconds."
    wav = await _run_piper(msg, model_path)
    if wav:
        await _play_wav_in_room(HELEN_ROOM, session_id, wav)

    while remaining > 0:
        await asyncio.sleep(_COUNTDOWN_INTERVAL)
        remaining -= _COUNTDOWN_INTERVAL

        # Check if this caller is still in the queue
        still_waiting = any(e["session_id"] == session_id for e in _caller_queue)
        if not still_waiting:
            break

        if remaining <= 0:
            msg = "You are next. Connecting you to Helen now."
        else:
            msg = f"Please hold. Your wait time is approximately {remaining} seconds."

        wav = await _run_piper(msg, model_path)
        if wav:
            await _play_wav_in_room(HELEN_ROOM, session_id, wav)


async def _play_wav_in_room(room_name: str, target_identity_prefix: str, wav_bytes: bytes):
    """
    Connect a temporary Piper TTS participant to the room and publish the WAV audio.
    Uses livekit-api to publish audio directly via RTC.
    Falls back to logging if rtc is not available.
    """
    try:
        from livekit import rtc
        from livekit.api import AccessToken, VideoGrants
        import os

        api_key    = os.getenv("LIVEKIT_API_KEY",    "devkey")
        api_secret = os.getenv("LIVEKIT_API_SECRET", "devsecret")
        lk_url     = os.getenv("LIVEKIT_URL",        "wss://sch-natyyy4y.livekit.cloud")

        identity = f"piper-tts-{uuid.uuid4().hex[:6]}"
        grants   = VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=False)
        token    = AccessToken(api_key, api_secret).with_identity(identity).with_grants(grants).to_jwt()

        room  = rtc.Room()
        source = rtc.AudioSource(sample_rate=22050, num_channels=1)
        track  = rtc.LocalAudioTrack.create_audio_track("piper-tts", source)

        await room.connect(lk_url, token)
        await room.local_participant.publish_track(track)

        # Push WAV frames
        import io, wave, numpy as np
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, 'rb') as wf:
            sr    = wf.getframerate()
            raw   = wf.readframes(wf.getnframes())

        pcm = np.frombuffer(raw, dtype=np.int16)
        frame_samples = sr // 50  # 20ms frames
        for i in range(0, len(pcm), frame_samples):
            chunk = pcm[i:i+frame_samples]
            if len(chunk) < frame_samples:
                chunk = np.pad(chunk, (0, frame_samples - len(chunk)))
            frame = rtc.AudioFrame(
                data=chunk.tobytes(),
                sample_rate=sr,
                num_channels=1,
                samples_per_channel=frame_samples,
            )
            await source.capture_frame(frame)

        await asyncio.sleep(0.5)
        await room.disconnect()
        logger.info("_play_wav_in_room: played %d bytes in %s", len(wav_bytes), room_name)
    except Exception as e:
        logger.warning("_play_wav_in_room failed: %s", e)


@receiver_router.delete("/livekit/caller-queue/{session_id}")
async def remove_from_queue(session_id: str):
    """Remove caller from queue when they disconnect — cancels their countdown."""
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
    return {
        "room":        HELEN_ROOM,
        "queue_depth": len(_caller_queue),
        "callers":     [
            {
                "session_id": e["session_id"],
                "wait_sec":   round(time.time() - e["joined_at"]),
            }
            for e in _caller_queue
        ],
        "wait_per_caller_sec": _WAIT_PER_CALLER_SEC,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy join-room alias
# ═══════════════════════════════════════════════════════════════════════════════

@receiver_router.get("/livekit/join-room")
async def join_room(
    room: str = Query(HELEN_ROOM),
    identity: str = Query("helen-receiver"),
    name: str = Query("Helen"),
):
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
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    model_path = _VOICE_MODELS.get(body.voice, _PIPER_MODEL)
    wav_bytes  = await _run_piper(text, model_path)

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
    exe   = Path(_PIPER_EXE)
    model = Path(model_path)

    if not exe.exists():
        logger.warning("Piper exe not found: %s", exe)
        return None
    if not model.exists():
        logger.warning("Piper model not found: %s", model)
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            str(exe), "--model", str(model), "--output_raw",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=text.encode("utf-8")), timeout=30.0
        )
        if proc.returncode != 0:
            logger.error("Piper error: %s", stderr.decode())
            return None
        return _raw_pcm_to_wav(stdout, sample_rate=22050)
    except asyncio.TimeoutError:
        logger.error("Piper TTS timed out")
        return None
    except Exception as e:
        logger.error("Piper TTS failed: %s", e)
        return None


def _raw_pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 22050) -> bytes:
    import wave, io
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()
