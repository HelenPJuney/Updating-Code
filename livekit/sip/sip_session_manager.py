"""
SIP Session Manager — maps SIP call_id ↔ session_id ↔ room_id.

This mapping layer is critical for:
    • Tracking active SIP calls end-to-end
    • Correlating LiveKit room events to SIP call state
    • Handling disconnect/hangup events from either side
    • Metrics and logging

Thread-safety:
    All mutations are protected by an asyncio.Lock so concurrent webhook
    calls don't corrupt state.

Call states:
    ringing    → SIP INVITE received, room being created
    connected  → AI worker joined, audio flowing
    completed  → Call ended normally (SIP BYE or AI hangup)
    failed     → Call setup or runtime failure
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger("callcenter.sip.session_manager")


class SipCallState(str, Enum):
    """SIP call lifecycle states."""
    RINGING   = "ringing"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED    = "failed"


@dataclass
class SipSession:
    """
    Represents one SIP call and its mapping to internal identifiers.

    Fields:
        sip_call_id   — Unique ID from the SIP INVITE (Call-ID header)
        session_id    — Internal session UUID (same as CallRequest.session_id)
        room_id       — LiveKit room name (same as CallRequest.room_id)
        state         — Current lifecycle state
        caller_number — SIP caller's phone number / URI
        created_at    — Epoch timestamp when session was created
        updated_at    — Epoch timestamp of last state change
        livekit_participant_id — SIP participant's identity in LiveKit
        metadata      — Arbitrary key-value metadata for extensibility
    """
    sip_call_id:  str
    session_id:   str
    room_id:      str
    state:        SipCallState = SipCallState.RINGING

    caller_number:           str = ""
    created_at:              float = field(default_factory=time.time)
    updated_at:              float = field(default_factory=time.time)
    livekit_participant_id:  str = ""
    metadata:                Dict[str, str] = field(default_factory=dict)

    def transition(self, new_state: SipCallState) -> None:
        """Move to a new state and update timestamp."""
        old = self.state
        self.state = new_state
        self.updated_at = time.time()
        logger.info(
            "[SipSession] %s → %s  sip_call=%s  session=%s",
            old.value, new_state.value,
            self.sip_call_id[:12], self.session_id[:8],
        )


class SipSessionManager:
    """
    Async-safe bidirectional mapping:
        sip_call_id → SipSession
        session_id  → SipSession
        room_id     → SipSession

    Provides O(1) lookup in any direction.
    """

    def __init__(self) -> None:
        self._by_sip_call:  Dict[str, SipSession] = {}
        self._by_session:   Dict[str, SipSession] = {}
        self._by_room:      Dict[str, SipSession] = {}
        self._lock = asyncio.Lock()

    # ── Create / Register ─────────────────────────────────────────────────────

    async def register(
        self,
        sip_call_id: str,
        session_id:  str,
        room_id:     str,
        caller_number: str = "",
        participant_id: str = "",
    ) -> SipSession:
        """
        Create and register a new SIP session mapping.
        Idempotent — returns existing session if sip_call_id already registered.
        """
        async with self._lock:
            if sip_call_id in self._by_sip_call:
                existing = self._by_sip_call[sip_call_id]
                logger.debug(
                    "[SipMgr] already registered  sip_call=%s", sip_call_id[:12]
                )
                return existing

            sess = SipSession(
                sip_call_id=sip_call_id,
                session_id=session_id,
                room_id=room_id,
                caller_number=caller_number,
                livekit_participant_id=participant_id,
            )
            self._by_sip_call[sip_call_id] = sess
            self._by_session[session_id]   = sess
            self._by_room[room_id]         = sess

        logger.info(
            "[SipMgr] registered  sip_call=%s  session=%s  room=%s  caller=%s",
            sip_call_id[:12], session_id[:8], room_id[:8], caller_number,
        )
        return sess

    # ── Lookups ───────────────────────────────────────────────────────────────

    def get_by_sip_call(self, sip_call_id: str) -> Optional[SipSession]:
        return self._by_sip_call.get(sip_call_id)

    def get_by_session(self, session_id: str) -> Optional[SipSession]:
        return self._by_session.get(session_id)

    def get_by_room(self, room_id: str) -> Optional[SipSession]:
        return self._by_room.get(room_id)

    # ── State transitions ─────────────────────────────────────────────────────

    async def update_state(
        self,
        session_id: str,
        new_state: SipCallState,
    ) -> Optional[SipSession]:
        """Transition a session to a new state. Returns None if not found."""
        async with self._lock:
            sess = self._by_session.get(session_id)
            if sess:
                sess.transition(new_state)
            return sess

    async def mark_connected(self, session_id: str) -> Optional[SipSession]:
        return await self.update_state(session_id, SipCallState.CONNECTED)

    async def mark_completed(self, session_id: str) -> Optional[SipSession]:
        return await self.update_state(session_id, SipCallState.COMPLETED)

    async def mark_failed(self, session_id: str) -> Optional[SipSession]:
        return await self.update_state(session_id, SipCallState.FAILED)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def remove(self, session_id: str) -> Optional[SipSession]:
        """
        Remove a session from all indices.
        Called after call ends and cleanup is complete.
        """
        async with self._lock:
            sess = self._by_session.pop(session_id, None)
            if sess:
                self._by_sip_call.pop(sess.sip_call_id, None)
                self._by_room.pop(sess.room_id, None)
                logger.info(
                    "[SipMgr] removed  session=%s  sip_call=%s",
                    session_id[:8], sess.sip_call_id[:12],
                )
            return sess

    async def remove_by_room(self, room_id: str) -> Optional[SipSession]:
        """Remove by room_id (used when LiveKit reports room closed)."""
        sess = self._by_room.get(room_id)
        if sess:
            return await self.remove(sess.session_id)
        return None

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def active_count(self) -> int:
        """Number of sessions that are NOT completed/failed."""
        return sum(
            1 for s in self._by_session.values()
            if s.state in (SipCallState.RINGING, SipCallState.CONNECTED)
        )

    @property
    def total_count(self) -> int:
        return len(self._by_session)

    def get_all_active(self) -> list[SipSession]:
        """Return all sessions in ringing or connected state."""
        return [
            s for s in self._by_session.values()
            if s.state in (SipCallState.RINGING, SipCallState.CONNECTED)
        ]

    def to_dict_list(self) -> list[dict]:
        """Serialise all sessions for API/debugging."""
        return [
            {
                "sip_call_id":  s.sip_call_id,
                "session_id":   s.session_id,
                "room_id":      s.room_id,
                "state":        s.state.value,
                "caller_number": s.caller_number,
                "created_at":   s.created_at,
                "updated_at":   s.updated_at,
            }
            for s in self._by_session.values()
        ]


# ── Module-level singleton ────────────────────────────────────────────────────
sip_session_mgr = SipSessionManager()
