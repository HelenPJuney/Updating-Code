
# [ START ]
#     |
#     v
# +--------------------------+
# | LiveKitSession()         |
# | * dataclass init         |
# +--------------------------+
#     |
#     |----> field(default_factory=list)  * init history
#     |
#     |----> AudioBuf()                   * init VAD buffer
#     |
#     |----> asyncio.Lock()               * init turn lock
#     |
#     v
# +--------------------------+
# | mark_interrupted()       |
# | * barge-in handler       |
# +--------------------------+
#     |
#     |----> self.audio_source.clear()
#     |
#     |----> _trim_last_ai_turn()
#     |
#     v
# +--------------------------+
# | _trim_last_ai_turn()     |
# | * recording management   |
# +--------------------------+
#     |
#     |----> recording_turns.reverse_scan()
#     |
#     |----> set trim_frames
#     |
#     v
# +--------------------------+
# | __repr__()               |
# | * debug string           |
# +--------------------------+
#     |
#     v
# [ YIELD ]


import asyncio
from dataclasses import dataclass, field
from typing import Any, List, Optional

from backend.core.vad import AudioBuf


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


    # ── Barge-in helpers ──────────────────────────────────────────────────────

    def mark_interrupted(self) -> None:
        
        self.interrupted = True
        if self.audio_source is not None:
            drained = self.audio_source.clear()
            self._trim_last_ai_turn(drained)

    def _trim_last_ai_turn(self, drained_frames: int) -> None:
       
        for i in range(len(self.recording_turns) - 1, -1, -1):
            if self.recording_turns[i]["type"] == "ai":
                if "trim_frames" not in self.recording_turns[i]:
                    self.recording_turns[i]["trim_frames"] = drained_frames
                break

    def __repr__(self) -> str:
        return (
            f"<LiveKitSession id={self.session_id[:8]} "
            f"lang={self.lang} llm={self.llm_key} "
            f"agent={self.agent_name} "
            f"connected={self.connected} closed={self.closed}>"
        )
