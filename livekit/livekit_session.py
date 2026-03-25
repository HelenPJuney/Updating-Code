"""
[ START ]
    |
    v
+--------------------------+
| LiveKitSession()         |
| * dataclass init         |
+--------------------------+
    |
    |----> field(default_factory=list)  * init history
    |
    |----> AudioBuf()                   * init VAD buffer
    |
    |----> asyncio.Lock()               * init turn lock
    |
    v
+--------------------------+
| mark_interrupted()       |
| * barge-in handler       |
+--------------------------+
    |
    |----> self.audio_source.clear()
    |
    |----> _trim_last_ai_turn()
    |
    v
+--------------------------+
| _trim_last_ai_turn()     |
| * recording management   |
+--------------------------+
    |
    |----> recording_turns.reverse_scan()
    |
    |----> set trim_frames
    |
    v
+--------------------------+
| __repr__()               |
| * debug string           |
+--------------------------+
    |
    v
[ YIELD ]
"""
# ==========================================================
# APPLICATION FLOW OVERVIEW
# ==========================================================
# 1. LiveKitSession            -> Per-call dataclass; holds room + audio_source
# 2. mark_interrupted()        -> Drain TTS queue + tag last AI turn for trim
# 3. _trim_last_ai_turn()      -> Tag recording turn with drained frame count
# 4. __repr__()                -> Short debug string for logging
#
# PIPELINE FLOW
# ai_worker_task() creates LiveKitSession
#    ||
# session.room = rtc.Room  (set after room.connect())
# session.audio_source = TtsAudioSource (set after track publish)
#    ||
# session.buf (AudioBuf) receives mic PCM from _inbound_audio_loop()
#    ||
# session.lock serialises concurrent _process_turn() calls
#    ||
# barge-in -> mark_interrupted() -> _trim_last_ai_turn()
#    ||
# hangup -> session.closed = True -> worker teardown
# ==========================================================

import asyncio
from dataclasses import dataclass, field
from typing import Any, List, Optional

from backend.core.vad import AudioBuf


# --------------------------------------------------
# LiveKitSession -> Per-call dataclass for LiveKit AI worker
#    ||
# room + audio_source -> LiveKit room + outbound TTS audio
#    ||
# buf + lock -> VAD buffer and turn serialisation lock (unchanged from WebRTC)
#    ||
# mark_interrupted -> clear() audio_source queue + tag last AI turn for trim
# --------------------------------------------------
@dataclass
class LiveKitSession:
    # ── Identity ─────────────────────────────────────────────────────────────
    session_id: str
    agent_name: str

    # ── Call parameters ───────────────────────────────────────────────────────
    lang:       str
    llm_key:    str
    voice_name: str
    model_path: str

    # ── LiveKit objects (set after room.connect()) ─────────────────────────────
    # Typed as Any to avoid importing livekit.rtc at module load time
    # (allows the module to load even when livekit is not yet installed)
    room:         Any = None   # livekit.rtc.Room
    audio_source: Any = None   # TtsAudioSource (backend/livekit/audio_source.py)

    # ── Conversation history ──────────────────────────────────────────────────
    history: List[dict] = field(default_factory=list)

    # ── Audio processing ──────────────────────────────────────────────────────
    buf: AudioBuf = field(default_factory=AudioBuf)

    # ── Concurrency ───────────────────────────────────────────────────────────
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ── Lifecycle flags ───────────────────────────────────────────────────────
    connected:   bool = False   # True once user participant joins room
    closed:      bool = False   # True after hangup / disconnect
    interrupted: bool = False   # True when barge-in received mid-turn

    # ── IVR backend integration ───────────────────────────────────────────────
    ivr_call_id:     Optional[int]  = None
    recording_turns: List[dict]     = field(default_factory=list)
    # Ordered turns for full-duplex recording:
    #   {"type": "ai",   "wav": bytes}        — Piper TTS WAV
    #   {"type": "user", "pcm": np.ndarray}   — float32 16 kHz PCM

    # ── Barge-in helpers ──────────────────────────────────────────────────────

    # --------------------------------------------------
    # mark_interrupted -> Set flag, drain TTS audio queue
    #    ||
    # audio_source.clear() -> _trim_last_ai_turn(drained)
    # --------------------------------------------------
    def mark_interrupted(self) -> None:
        """
        Called when the user interrupts the AI mid-speech.
        Drains the outbound TTS queue so audio stops immediately,
        then tags the last AI recording turn with the drained frame count
        so the recording is trimmed to the portion that was actually played.
        """
        self.interrupted = True
        if self.audio_source is not None:
            drained = self.audio_source.clear()
            self._trim_last_ai_turn(drained)

    # --------------------------------------------------
    # _trim_last_ai_turn -> Tag last AI turn with drained frame count
    #    ||
    # recording_turns reverse scan -> set trim_frames on first ai entry
    # --------------------------------------------------
    def _trim_last_ai_turn(self, drained_frames: int) -> None:
        """
        Tag the most recent AI recording turn with the number of 48 kHz
        frames that were drained (queued but never played).

        Only the first call wins — a second clear() returning 0 after the
        queue is already empty must not overwrite a real trim value.
        """
        for i in range(len(self.recording_turns) - 1, -1, -1):
            if self.recording_turns[i]["type"] == "ai":
                if "trim_frames" not in self.recording_turns[i]:
                    self.recording_turns[i]["trim_frames"] = drained_frames
                break

    # --------------------------------------------------
    # __repr__ -> Short debug string
    # --------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"<LiveKitSession id={self.session_id[:8]} "
            f"lang={self.lang} llm={self.llm_key} "
            f"agent={self.agent_name} "
            f"connected={self.connected} closed={self.closed}>"
        )
