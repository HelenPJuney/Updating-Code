"""
[ START ]
    |
    v
+--------------------------+
| get_livekit_token()      |
| * FastAPI JWT endpoint   |
+--------------------------+
    |
    |----> <_RateLimiter> -> check()
    |           |
    |           ----> asyncio.get_running_loop()
    |
    |----> generate_token()
    |
    |----> [ KAFKA PATH ]
    |           |
    |           ----> <CallRequestProducer> -> submit_call_request()
    |
    |----> [ FALLBACK PATH ]
                |
                ----> asyncio.ensure_future() -> ai_worker_task()
    |
    v
+--------------------------+
| ai_worker_task()         |
| * AI agent lifecycle     |
+--------------------------+
    |
    |----> <rtc.Room> -> connect()
    |
    |----> <TtsAudioSource> -> start()
    |
    |----> <rtc.LocalAudioTrack> -> create_audio_track()
    |
    |----> <livekit_session_manager> -> add()
    |
    |----> _register_ivr_call()
    |           |
    |           ----> _ivr_post() * "/calls/start"
    |
    |----> _send_greeting()
    |           |
    |           ----> _piper_sync()
    |           |
    |           ----> <TtsAudioSource> -> push_tts_wav()
    |           |
    |           ----> _publish_data() * {"type": "greeting"}
    |
    |----> _inbound_audio_loop()
    |           |
    |           ----> <rtc.AudioStream>
    |           |
    |           ----> <AudioBuf> -> push()
    |           |
    |           ----> _process_turn()
    |                       |
    |                       |----> stt_sync()
    |                       |
    |                       |----> _gemini_sync() / _qwen_sync()
    |                       |
    |                       |----> _piper_sync()
    |                       |
    |                       |----> _publish_data() * {"type": "response"}
    |
    v
+--------------------------+
| _finalize_ivr_call()     |
| * teardown and recording |
+--------------------------+
    |
    |----> _build_recording()
    |
    |----> _ivr_patch() * "/calls/recording"
    |
    |----> _ivr_post()  * "/calls/end"
    |
[ END ]
"""

# ==========================================================
# APPLICATION FLOW OVERVIEW
# ==========================================================
# 1.  get_livekit_token()      -> Issue browser JWT + submit to Kafka
# 2.  _RateLimiter             -> Token-bucket per-process rate limiter
# 3.  livekit_health()         -> Return session count + Kafka status
# 4.  get_queue_status()       -> Read cached Kafka lag (zero I/O)
# 5.  ai_worker_task()         -> Full lifecycle of one AI call session
# 6.  _send_greeting()         -> TTS-synthesize and publish opening line
# 7.  _inbound_audio_loop()    -> Receive mic frames, feed VAD buffer
# 8.  _process_turn()          -> STT → LLM → TTS for one utterance
# 9.  _publish_data()          -> Send JSON control msg via DataChannel
# 10. _build_recording()       -> Assemble full-duplex WAV at hangup
# 11. _finalize_ivr_call()     -> Save recording + close IVR record
# 12. _register_ivr_call()     -> Register call in ivr_backend
# 13. _save_transcript()       -> Persist one transcript line to IVR
#
# PIPELINE FLOW
# Browser GET /livekit/token
#    ||
# get_livekit_token() -> rate-limit check -> Kafka produce (or fallback spawn)
#    ||
# ai_worker_task() -> room.connect -> publish TTS audio track
#    ||
# participant_connected -> _send_greeting() -> _piper_sync -> push_tts_wav()
#    ||
# _inbound_audio_loop() -> AudioBuf VAD -> _process_turn()
#    ||
# stt_sync -> _collapse_repetitions -> _is_hallucination
#    ||
# _gemini_sync / _qwen_sync -> _humanize_text -> _piper_sync
#    ||
# push_tts_wav() -> _publish_data(response) -> _save_transcript()
#    ||
# hangup -> _finalize_ivr_call() -> _build_recording() -> room.disconnect()
# ==========================================================

import asyncio
import io
import json
import logging
import random
import uuid
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException

from .audio_source import TtsAudioSource
from .livekit_session import LiveKitSession
from .session_manager import livekit_session_manager
from .token_service import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, generate_token

from backend.core.config import LANGUAGE_CONFIG
from backend.core.state import _m
from backend.core.persona import extract_agent_name, generate_greeting
from backend.core.stt import stt_sync, _collapse_repetitions, _is_hallucination
from backend.core.tts import _piper_sync, _humanize_text
from backend.core.llm import _gemini_sync, _qwen_sync
from backend.services.greeting_loader import load_greetings

logger = logging.getLogger("callcenter.livekit.worker")

# ── Constants ─────────────────────────────────────────────────────────────────
_IVR_BASE        = "http://localhost:8001"
_IVR_RECORDINGS  = Path(__file__).parent.parent.parent / "ivr_backend" / "recordings"

# Worker participant identity prefix (keeps it distinguishable from users)
_WORKER_IDENTITY_PREFIX = "ai-worker-"

# FastAPI router — mounted at /livekit prefix by app.py
livekit_router = APIRouter(prefix="/livekit", tags=["livekit"])


# ── In-memory rate limiter (Fix 7) ───────────────────────────────────────────
# Token-bucket algorithm — no external dependencies.
# Allows up to _RATE_MAX_CALLS requests per _RATE_WINDOW_SEC window.
# Implemented as a simple in-process counter; suitable for single-process
# deployments. For multi-process deployments use Redis atomic increment.

class _RateLimiter:
    """Thread/coroutine safe token-bucket rate limiter."""

    def __init__(self, max_calls: int, window_sec: float) -> None:
        self._max_calls  = max_calls
        self._window_sec = window_sec
        self._calls: List[float] = []   # timestamps of recent requests
        self._lock = asyncio.Lock()

    async def check(self) -> bool:
        """
        Return True if the request is allowed, False if rate-limited.
        Slides the window and prunes old timestamps every call.
        """
        now = asyncio.get_running_loop().time()
        async with self._lock:
            # Remove timestamps outside the current window
            cutoff = now - self._window_sec
            self._calls = [t for t in self._calls if t > cutoff]
            if len(self._calls) >= self._max_calls:
                return False
            self._calls.append(now)
            return True


_token_rate_limiter = _RateLimiter(max_calls=100, window_sec=1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# PART A — FastAPI endpoints
# ═══════════════════════════════════════════════════════════════════════════════

# --------------------------------------------------
# get_livekit_token -> Issue JWT + submit call to Kafka scheduler
#    ||
# Resolve voice registry -> generate user JWT
#    ||
# KAFKA PATH:   produce call_request → Kafka → Scheduler → WorkerService
# FALLBACK PATH: asyncio.ensure_future(ai_worker_task) [Kafka unavailable]
#    ||
# Return {token, url, room, agent_name, session_id, queue_position}
# --------------------------------------------------
@livekit_router.get("/token")
async def get_livekit_token(
    lang:  str = "en",
    llm:   str = "gemini",
    voice: str = "",
):
    """
    Issue a LiveKit JWT for the browser and submit the call to the Kafka
    scheduler for GPU-aware dispatch.

    Query params:
        lang  — ISO language code (en, hi, te, …)
        llm   — "gemini" or "qwen"
        voice — Piper voice stem (e.g. "en_US-lessac-medium")

    Returns:
        {
          "token":           "<signed JWT for browser>",
          "url":             "ws://localhost:7880",
          "room":            "<room UUID>",
          "agent_name":      "Angela",
          "session_id":      "<uuid>",
          "queue_position":  null | int   # null = starting immediately
        }

    Kafka path (production):
        The browser JWT is returned immediately so the browser can join the
        LiveKit room.  The AI worker is started asynchronously by the GPU
        Worker Service once the Scheduler gives the green light.  While
        waiting, the browser receives queue_update DataChannel messages.

    Fallback path (Kafka unavailable):
        Falls back to the original behaviour — ai_worker_task is spawned
        directly.  queue_position is always null in this case.
    """
    room_id    = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # FIX 7: Rate limiting — 100 req/s per process
    if not await _token_rate_limiter.check():
        logger.warning("[Token] rate limit exceeded — returning 429")
        raise HTTPException(status_code=429, detail="Too many requests")

    # ── Resolve agent name from voice registry ────────────────────────────────
    registry    = _m.get("voice_registry", {})
    lang_voices = registry.get(lang) or registry.get("en") or []
    selected    = (
        next((v for v in lang_voices if v["name"] == voice), None)
        or (lang_voices[0] if lang_voices else None)
    )
    voice_stem = selected["name"]       if selected else voice
    model_path = selected["model_path"] if selected else ""
    agent_name = extract_agent_name(voice_stem)

    # ── Generate browser JWT (user can publish mic + subscribe to AI audio) ───
    user_token = generate_token(
        room_name    = room_id,
        identity     = f"user-{session_id[:8]}",
        name         = "Caller",
        can_publish  = True,
        can_subscribe= True,
    )

    # ── Submit call to Kafka scheduler (or fall back to direct spawn) ─────────
    queue_position: Optional[int] = None
    kafka_dispatched: bool = False

    try:
        from .kafka.producer import get_producer
        from .kafka.schemas  import CallRequest as KafkaCallRequest

        producer = get_producer()
        if producer.is_kafka_active:
            req = KafkaCallRequest(
                session_id = session_id,
                room_id    = room_id,
                lang       = lang,
                llm        = llm,
                voice      = voice_stem,
                model_path = model_path,
                agent_name = agent_name,
            )
            queue_position   = await producer.submit_call_request(req)
            kafka_dispatched = True
            logger.info(
                "[Token] kafka dispatch  session=%s  room=%s  queue_pos=%s",
                session_id[:8], room_id[:8], queue_position,
            )
    except Exception as _kafka_err:
        logger.warning(
            "[Token] Kafka dispatch failed (%s) — falling back to direct spawn",
            _kafka_err,
        )

    # ── Fallback: spawn directly if Kafka is unavailable ─────────────────────
    if not kafka_dispatched:
        asyncio.ensure_future(
            ai_worker_task(
                room_id    = room_id,
                session_id = session_id,
                lang       = lang,
                llm_key    = llm,
                voice_stem = voice_stem,
                model_path = model_path,
                agent_name = agent_name,
            )
        )
        logger.info(
            "[Token] direct spawn (no Kafka)  session=%s  room=%s  lang=%s llm=%s voice=%s agent=%s",
            session_id[:8], room_id[:8], lang, llm, voice_stem, agent_name,
        )

    return {
        "token":          user_token,
        "url":            LIVEKIT_URL,
        "room":           room_id,
        "agent_name":     agent_name,
        "session_id":     session_id,
        "queue_position": queue_position,   # null if starting immediately
    }


@livekit_router.get("/health")
async def livekit_health():
    """Return current session count and Kafka status — useful for monitoring."""
    from .kafka.producer import get_producer
    producer = get_producer()
    return {
        "status":          "ok",
        "active_sessions": livekit_session_manager.count,
        "livekit_url":     LIVEKIT_URL,
        "api_key":         LIVEKIT_API_KEY,
        "kafka_active":    producer.is_kafka_active,
    }


@livekit_router.get("/queue-status/{session_id}")
async def get_queue_status(session_id: str):
    """
    Poll the approximate queue depth for a waiting session.

    FIX 4: No Kafka consumer is created per request.  The Scheduler
    periodically refreshes a cached lag value (_cached_lag) which is
    exposed via get_cached_lag().  This endpoint reads that integer
    directly — O(1), no I/O.

    Returns:
        {
          "session_id":      "<uuid>",
          "status":          "waiting" | "active" | "unknown",
          "queue_position":  int | null,
          "eta_sec":         int | null
        }
    """
    # 1. Check if the session is actively running locally
    if livekit_session_manager.get(session_id):
        return {
            "session_id":     session_id,
            "status":         "active",
            "queue_position": None,
            "eta_sec":        None,
        }

    # 2. Read the cached Kafka lag from the Scheduler (zero-cost, no I/O)
    from .kafka.scheduler import get_cached_lag
    from .kafka.config    import AVG_CALL_DURATION_SEC
    lag = get_cached_lag()

    if lag > 0:
        return {
            "session_id":     session_id,
            "status":         "waiting",
            "queue_position": lag,
            "eta_sec":        lag * AVG_CALL_DURATION_SEC,
        }

    return {
        "session_id":     session_id,
        "status":         "unknown",
        "queue_position": None,
        "eta_sec":        None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PART B — IVR backend HTTP helpers  (identical to old signaling_server.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _ivr_post(path: str, body: dict) -> Optional[dict]:
    """Synchronous POST to ivr_backend — run via run_in_executor."""
    try:
        import requests as _req
        r = _req.post(f"{_IVR_BASE}{path}", json=body, timeout=3.0)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None


def _ivr_patch(path: str, body: dict) -> None:
    try:
        import requests as _req
        _req.patch(f"{_IVR_BASE}{path}", json=body, timeout=3.0)
    except Exception:
        pass


async def _register_ivr_call(session: LiveKitSession) -> None:
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, _ivr_post, "/calls/start", {
        "caller_number": f"LiveKit-{session.session_id[:8]}",
        "department":    "AI Call",
    })
    if data and data.get("id"):
        session.ivr_call_id = data["id"]
        logger.info("[IVR] call registered  call_id=%s", session.ivr_call_id)


async def _save_transcript(call_id: int, speaker: str, text: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _ivr_post,
        f"/calls/{call_id}/transcript",
        {"speaker": speaker, "text": text},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PART C — Recording builder  (identical to old signaling_server.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_recording(turns: List[dict]) -> Optional[bytes]:
    """
    Build full-duplex WAV from ordered recording turns.
    Identical to old signaling_server._build_recording() — do not modify.

    Each turn is:
      {"type": "ai",   "wav": bytes}        — Piper TTS WAV
      {"type": "user", "pcm": np.ndarray}   — float32 16 kHz PCM
    """
    from backend.webrtc.utils import wav_bytes_to_pcm, resample_audio

    TARGET_SR   = 16_000
    GAP_SAMPLES = int(TARGET_SR * 0.12)
    all_pcm: List[np.ndarray] = []

    for turn in turns:
        try:
            if turn["type"] == "ai":
                pcm_f32, sr = wav_bytes_to_pcm(turn["wav"])
                if sr != TARGET_SR:
                    pcm_f32 = resample_audio(pcm_f32, sr, TARGET_SR)
                trim_frames = turn.get("trim_frames", 0)
                if trim_frames > 0:
                    trim_samples = trim_frames * 320   # 960 @ 48kHz → 320 @ 16kHz
                    if trim_samples >= len(pcm_f32):
                        continue
                    pcm_f32 = pcm_f32[:-trim_samples]
                all_pcm.append(pcm_f32)
            elif turn["type"] == "user":
                all_pcm.append(turn["pcm"].astype(np.float32))
            all_pcm.append(np.zeros(GAP_SAMPLES, dtype=np.float32))
        except Exception:
            continue

    if not all_pcm:
        return None

    combined = np.concatenate(all_pcm)
    pcm_i16  = (np.clip(combined, -1.0, 1.0) * 32767).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_SR)
        wf.writeframes(pcm_i16.tobytes())
    return out.getvalue()


async def _finalize_ivr_call(session: LiveKitSession) -> None:
    """Save recording + end call record in ivr_backend. Identical to old code."""
    if not session.ivr_call_id:
        return
    loop = asyncio.get_running_loop()

    if session.recording_turns:
        wav_data = _build_recording(session.recording_turns)
        if wav_data:
            try:
                _IVR_RECORDINGS.mkdir(exist_ok=True)
                filename = f"{session.ivr_call_id}.wav"
                (_IVR_RECORDINGS / filename).write_bytes(wav_data)
                await loop.run_in_executor(None, _ivr_patch,
                    f"/calls/{session.ivr_call_id}/recording",
                    {"recording_path": filename},
                )
                logger.info(
                    "[IVR] recording saved  call_id=%s  file=%s",
                    session.ivr_call_id, filename,
                )
            except Exception:
                logger.debug("[IVR] recording save failed  call_id=%s", session.ivr_call_id)

    await loop.run_in_executor(None, _ivr_post,
        f"/calls/{session.ivr_call_id}/end", {},
    )
    logger.info("[IVR] call ended  call_id=%s", session.ivr_call_id)


# ═══════════════════════════════════════════════════════════════════════════════
# PART D — Data channel helper
# ═══════════════════════════════════════════════════════════════════════════════

# --------------------------------------------------
# _publish_data -> Send JSON control message to browser via LiveKit DataChannel
#    ||
# room.local_participant.publish_data(payload, reliable=True)
# --------------------------------------------------
async def _publish_data(session: LiveKitSession, msg: dict) -> None:
    """
    Publish a JSON control message to all participants in the room.
    Replaces the old ws.send_json() calls — same message format, different transport.
    """
    if session.room is None or session.closed:
        return
    try:
        await session.room.local_participant.publish_data(
            payload = json.dumps(msg).encode("utf-8"),
            reliable = True,
        )
    except Exception:
        pass   # room may have closed; swallow silently


# ═══════════════════════════════════════════════════════════════════════════════
# PART E — AI Greeting
# ═══════════════════════════════════════════════════════════════════════════════

# --------------------------------------------------
# _send_greeting -> TTS-synthesize the opening greeting and publish to room
#    ||
# load_greetings / generate_greeting -> _piper_sync -> push_tts_wav
#    ||
# _publish_data {"type":"greeting"} + save to ivr transcript
# --------------------------------------------------
async def _send_greeting(session: LiveKitSession) -> None:
    loop = asyncio.get_running_loop()
    try:
        greetings    = load_greetings()
        raw_greeting = (
            greetings.get(session.lang)
            or generate_greeting(session.lang, session.agent_name)
        )
        greeting_text = raw_greeting.format(name=session.agent_name)
        session.history.append({"role": "assistant", "text": greeting_text})

        wav_bytes = await loop.run_in_executor(
            None, _piper_sync, greeting_text, session.model_path
        )
        await session.audio_source.push_tts_wav(wav_bytes)
        session.recording_turns.append({"type": "ai", "wav": wav_bytes})

        await _publish_data(session, {
            "type":       "greeting",
            "text":       greeting_text,
            "agent_name": session.agent_name,
        })

        if session.ivr_call_id:
            asyncio.ensure_future(
                _save_transcript(session.ivr_call_id, "agent", greeting_text)
            )

        logger.info(
            "[Greeting] sent  session=%s  text=%r",
            session.session_id[:8], greeting_text[:60],
        )
    except Exception:
        logger.exception("[Greeting] error  session=%s", session.session_id[:8])


# ═══════════════════════════════════════════════════════════════════════════════
# PART F — Inbound audio loop
# ═══════════════════════════════════════════════════════════════════════════════

# --------------------------------------------------
# _inbound_audio_loop -> Receive LiveKit audio frames, feed VAD buffer
#    ||
# rtc.AudioStream(track, sample_rate=16000) -> int16 @ 16kHz
#    ||
# frombuffer -> float32 -> AudioBuf.push
#    ||
# buf.ready -> audio_source.clear (barge-in) -> _process_turn
# --------------------------------------------------
async def _inbound_audio_loop(session: LiveKitSession, track) -> None:
    """
    Consume audio frames from the user's LiveKit audio track and feed them
    into the AudioBuf VAD.

    Key difference from the old aiortc loop:
      OLD: frame.to_ndarray() + manual 48→16kHz resample
      NEW: rtc.AudioStream(..., sample_rate=16000) — LiveKit resamples for us.
           Frames arrive already at 16 kHz mono, so no scipy resample needed.
    """
    from livekit import rtc

    logger.info(
        "[Inbound] audio loop started  session=%s",
        session.session_id[:8],
    )

    # Request 16kHz mono from LiveKit — matches STT and VAD input exactly.
    # No manual resampling step needed (unlike the old aiortc path).
    #
    # Compatibility note:
    #   livekit-rtc iterates directly: `async for event in AudioStream(...)`
    #   event may be AudioFrameEvent (has .frame) or AudioFrame directly.
    #   We handle both with getattr fallback.
    try:
        stream = rtc.AudioStream(track, sample_rate=16_000, num_channels=1)
    except TypeError:
        # Older SDK versions may not accept keyword args
        stream = rtc.AudioStream(track)

    async for event in stream:
        if session.closed:
            break

        # Handle both AudioFrameEvent(.frame) and direct AudioFrame
        frame = getattr(event, "frame", event)
        raw   = getattr(frame, "data", None)
        if raw is None:
            continue
        pcm_int16 = np.frombuffer(bytes(raw), dtype=np.int16)
        pcm_f32   = pcm_int16.astype(np.float32) / 32768.0

        # If SDK didn't resample for us, do it manually
        sr = getattr(frame, "sample_rate", 16_000)
        if sr != 16_000:
            from backend.webrtc.utils import resample_audio
            pcm_f32 = resample_audio(pcm_f32, sr, 16_000)

        session.buf.push(pcm_f32)

        if session.buf.ready() and not session.lock.locked():
            pcm_utt = session.buf.flush()
            if pcm_utt is not None:
                # Save user PCM for full-duplex recording
                session.recording_turns.append({"type": "user", "pcm": pcm_utt})

                # Auto barge-in: drain TTS queue, tag recording, notify browser
                drained = session.audio_source.clear()
                session._trim_last_ai_turn(drained)
                await _publish_data(session, {"type": "barge_in"})

                asyncio.ensure_future(_process_turn(pcm_utt, session))

    logger.info(
        "[Inbound] audio loop ended  session=%s",
        session.session_id[:8],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PART G — Per-turn AI pipeline  (identical to old signaling_server._process_turn)
# ═══════════════════════════════════════════════════════════════════════════════

# --------------------------------------------------
# _process_turn -> Full AI pipeline for one speech turn
#    ||
# stt_sync -> _collapse_repetitions -> _is_hallucination
#    ||
# _gemini_sync / _qwen_sync -> _humanize_text -> _piper_sync
#    ||
# push_tts_wav -> _publish_data response -> _save_transcript -> FAISS persist
# --------------------------------------------------
async def _process_turn(pcm: np.ndarray, session: LiveKitSession) -> None:
    """
    Run the full AI pipeline for one utterance:
      STT → LLM → TTS → publish audio + data messages

    All logic is identical to old signaling_server._process_turn().
    The only difference: instead of ws.send_json(), we call _publish_data().
    Instead of outbound_track.push_tts_wav(), we call audio_source.push_tts_wav().
    """
    loop = asyncio.get_running_loop()

    async with session.lock:

        # Clear stale interrupt from before this turn started.
        # (Same logic as old code — prevents pivot on the user's own question.)
        session.interrupted = False

        # ── Stage 1: STT ──────────────────────────────────────────────────────
        try:
            user_text: str = await loop.run_in_executor(
                None, stt_sync, pcm, session.lang
            )
        except Exception:
            logger.exception("[Turn] STT error  session=%s", session.session_id[:8])
            session.buf.flush()
            return

        if not user_text:
            return

        user_text = _collapse_repetitions(user_text)
        if _is_hallucination(user_text):
            logger.warning("[Turn] hallucination dropped  session=%s", session.session_id[:8])
            return

        await _publish_data(session, {"type": "transcript", "text": user_text})
        session.history.append({"role": "user", "text": user_text})
        hist_snap = list(session.history)

        if session.ivr_call_id:
            asyncio.ensure_future(
                _save_transcript(session.ivr_call_id, "caller", user_text)
            )

        # ── Stage 2: LLM ──────────────────────────────────────────────────────
        llm_fn = _gemini_sync if session.llm_key == "gemini" else _qwen_sync
        logger.info("[Turn] LLM start  llm=%s  session=%s", session.llm_key, session.session_id[:8])
        try:
            ai_text: str = await loop.run_in_executor(
                None, llm_fn, hist_snap, session.lang, session.voice_name
            )
        except Exception:
            logger.exception("[Turn] LLM error  session=%s", session.session_id[:8])
            session.buf.flush()
            canned = LANGUAGE_CONFIG.get(session.lang, LANGUAGE_CONFIG["en"]).get(
                "canned_error", "Sorry, I had a connection issue. Could you repeat that?"
            )
            await _publish_data(session, {"type": "response", "text": canned})
            return

        logger.info("[Turn] LLM done  text=%r  session=%s", ai_text[:60] if ai_text else "", session.session_id[:8])

        if not ai_text:
            return

        await asyncio.sleep(random.uniform(0.2, 0.5))

        # ── Stage 3: Barge-in pivot ───────────────────────────────────────────
        if session.interrupted:
            session.interrupted = False
            barge_text = random.choice(
                LANGUAGE_CONFIG.get(session.lang, LANGUAGE_CONFIG["en"])["barge_phrases"]
            )
            logger.info("[Turn] barge-in pivot=%r  session=%s", barge_text, session.session_id[:8])
            session.history.append({"role": "assistant", "text": barge_text})
            try:
                barge_wav = await loop.run_in_executor(
                    None, _piper_sync, barge_text, session.model_path
                )
                await session.audio_source.push_tts_wav(barge_wav)
                session.recording_turns.append({"type": "ai", "wav": barge_wav})
            except Exception:
                logger.exception("[Turn] barge-in TTS error  session=%s", session.session_id[:8])
            await _publish_data(session, {
                "type": "response", "text": barge_text, "barge_in": True
            })
            return

        # ── Stage 4: TTS ──────────────────────────────────────────────────────
        session.history.append({"role": "assistant", "text": ai_text})
        tts_text = _humanize_text(ai_text, session.lang)

        logger.info("[Turn] TTS start  session=%s", session.session_id[:8])
        try:
            wav_bytes = await loop.run_in_executor(
                None, _piper_sync, tts_text, session.model_path
            )
            logger.info("[Turn] TTS done  session=%s", session.session_id[:8])
            await session.audio_source.push_tts_wav(wav_bytes)
            session.recording_turns.append({"type": "ai", "wav": wav_bytes})
        except Exception:
            logger.exception("[Turn] TTS error  session=%s", session.session_id[:8])

        await _publish_data(session, {"type": "response", "text": ai_text})

        if session.ivr_call_id:
            asyncio.ensure_future(
                _save_transcript(session.ivr_call_id, "agent", ai_text)
            )

        # ── Stage 5: FAISS memory persist (fire-and-forget) ──────────────────
        if _m.get("memory"):
            _u, _a, _l = user_text, ai_text, session.lang

            async def _persist() -> None:
                try:
                    await loop.run_in_executor(
                        None, _m["memory"].save_interaction, _u, _a, _l
                    )
                except Exception as exc:
                    logger.debug("[Turn] FAISS error: %s  session=%s", exc, session.session_id[:8])

            asyncio.create_task(_persist())


# ═══════════════════════════════════════════════════════════════════════════════
# PART H — Main worker task
# ═══════════════════════════════════════════════════════════════════════════════

# --------------------------------------------------
# ai_worker_task -> Full lifecycle of one AI call session
#    ||
# room.connect -> subscribe mic track -> publish TTS track
#    ||
# send greeting on user join -> run until session.closed
#    ||
# _finalize_ivr_call -> room.disconnect -> cleanup session
# --------------------------------------------------
async def ai_worker_task(
    room_id:    str,
    session_id: str,
    lang:       str,
    llm_key:    str,
    voice_stem: str,
    model_path: str,
    agent_name: str,
) -> None:
    """
    Background asyncio task — the AI call center agent for one room.

    Lifecycle:
      1. Create LiveKitSession
      2. Connect to LiveKit room as "ai-worker-{session_id[:8]}"
      3. Create TtsAudioSource + LocalAudioTrack, publish to room
      4. Register room event handlers (track_subscribed, data_received, …)
      5. Register ivr_backend call record
      6. If user is already in room → send greeting immediately
      7. Sleep in a loop until session.closed (set by hangup / disconnect)
      8. Finalize ivr_backend record + save recording
      9. Disconnect from room, cleanup session
    """
    from livekit import rtc

    session = LiveKitSession(
        session_id = session_id,
        agent_name = agent_name,
        lang       = lang,
        llm_key    = llm_key,
        voice_name = voice_stem,
        model_path = model_path,
    )

    # ── Connect to LiveKit ─────────────────────────────────────────────────────
    room = rtc.Room()
    session.room = room

    worker_token = generate_token(
        room_name     = room_id,
        identity      = f"{_WORKER_IDENTITY_PREFIX}{session_id[:8]}",
        name          = agent_name,
        can_publish   = True,
        can_subscribe = True,
    )

    # ── Room event handlers ────────────────────────────────────────────────────

    @room.on("participant_connected")
    def _on_participant_connected(participant) -> None:
        # Fire greeting when the first human caller joins.
        # Guard: the worker itself might trigger this on connect in some versions.
        ident: str = getattr(participant, "identity", "") or ""
        if _WORKER_IDENTITY_PREFIX in ident:
            return   # ignore our own join event
        if not session.connected:
            session.connected = True
            logger.info(
                "[Worker] user joined  participant=%s  session=%s",
                ident[:16], session.session_id[:8],
            )
            asyncio.ensure_future(_send_greeting(session))

    @room.on("participant_disconnected")
    def _on_participant_disconnected(participant) -> None:
        # End the call if the user leaves (browser closed / tab closed)
        ident: str = getattr(participant, "identity", "") or ""
        if _WORKER_IDENTITY_PREFIX in ident:
            return   # ignore worker's own events
        remaining = [
            p for p in room.remote_participants.values()
            if _WORKER_IDENTITY_PREFIX not in (getattr(p, "identity", "") or "")
        ]
        if not remaining:
            logger.info(
                "[Worker] user disconnected — ending session  session=%s",
                session.session_id[:8],
            )
            session.closed = True

    @room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant) -> None:
        # Subscribe to the user's microphone track.
        # Use isinstance as the primary check — track.kind is an int (1=audio)
        # in livekit-rtc, so str(track.kind) is "1" not "AUDIO".
        ident: str = getattr(participant, "identity", "") or ""
        if _WORKER_IDENTITY_PREFIX in ident:
            return   # don't subscribe to our own track

        is_audio = isinstance(track, rtc.RemoteAudioTrack)
        if not is_audio:
            # Fallback: check numeric kind value (1 = audio in TrackKind proto)
            kind_val = getattr(track, "kind", None)
            is_audio = (kind_val == 1 or kind_val == rtc.TrackKind.KIND_AUDIO
                        if hasattr(rtc, "TrackKind") else kind_val == 1)

        if is_audio:
            logger.info(
                "[Worker] subscribing to mic track  session=%s  participant=%s",
                session.session_id[:8], ident[:16],
            )
            asyncio.ensure_future(_inbound_audio_loop(session, track))
        else:
            logger.debug(
                "[Worker] ignoring non-audio track kind=%s  session=%s",
                getattr(track, "kind", "?"), session.session_id[:8],
            )

    @room.on("data_received")
    def _on_data_received(data_packet) -> None:
        """Handle control messages from the browser (interrupt, hangup)."""
        try:
            raw  = getattr(data_packet, "data", data_packet)
            msg  = json.loads(bytes(raw).decode("utf-8"))
            mtype = msg.get("type", "")

            if mtype == "interrupt":
                session.mark_interrupted()
                logger.info("[Worker] barge-in  session=%s", session.session_id[:8])

            elif mtype == "hangup":
                logger.info("[Worker] hangup received  session=%s", session.session_id[:8])
                session.closed = True

        except Exception:
            pass

    @room.on("disconnected")
    def _on_disconnected(*args) -> None:
        session.closed = True

    # ── Connect to LiveKit server ─────────────────────────────────────────────
    try:
        await room.connect(LIVEKIT_URL, worker_token)
        logger.info(
            "[Worker] connected to room  session=%s  room=%s",
            session.session_id[:8], room_id[:8],
        )
    except Exception:
        logger.exception(
            "[Worker] failed to connect to LiveKit  session=%s", session.session_id[:8]
        )
        return

    # ── Create TTS audio source and publish to room ───────────────────────────
    try:
        audio_source       = TtsAudioSource()
        session.audio_source = audio_source
        audio_source.start()   # start the _pump() coroutine

        ai_track = rtc.LocalAudioTrack.create_audio_track(
            "ai-voice", audio_source.source
        )
        publish_options = rtc.TrackPublishOptions(
            source = rtc.TrackSource.SOURCE_MICROPHONE,  # closest to voice
        )
        await room.local_participant.publish_track(ai_track, publish_options)
        logger.info("[Worker] audio track published  session=%s", session.session_id[:8])
    except Exception:
        logger.exception("[Worker] failed to publish audio track  session=%s", session.session_id[:8])
        await room.disconnect()
        return

    # ── Register call in ivr_backend ─────────────────────────────────────────
    await livekit_session_manager.add(session)
    asyncio.ensure_future(_register_ivr_call(session))

    # ── If user is already in the room (joined before worker) ─────────────────
    for participant in room.remote_participants.values():
        ident = getattr(participant, "identity", "") or ""
        if _WORKER_IDENTITY_PREFIX not in ident and not session.connected:
            session.connected = True
            logger.info(
                "[Worker] user already in room  session=%s",
                session.session_id[:8],
            )
            asyncio.ensure_future(_send_greeting(session))
            break

    # ── Main wait loop — everything happens via event handlers ────────────────
    logger.info("[Worker] waiting for call events  session=%s", session.session_id[:8])
    while not session.closed:
        await asyncio.sleep(0.5)

    # ── Teardown ──────────────────────────────────────────────────────────────
    logger.info("[Worker] session ending  session=%s", session.session_id[:8])

    # Stop TTS pump
    if session.audio_source:
        session.audio_source.stop()

    # Notify browser the call is over
    try:
        await _publish_data(session, {"type": "hangup"})
    except Exception:
        pass

    # Save recording + end ivr_backend record
    asyncio.ensure_future(_finalize_ivr_call(session))

    # Cleanup session registry
    await livekit_session_manager.cleanup_session(session.session_id)

    # Disconnect from LiveKit room
    try:
        await room.disconnect()
    except Exception:
        pass

    logger.info("[Worker] task complete  session=%s", session.session_id[:8])
