
# [ START ]
#     |
#     v
# +--------------------------+
# | add()                    |
# | * register new session   |
# +--------------------------+
#     |
#     |----> asyncio.Lock()  * thread-safe acquisition
#     |
#     |----> self._sessions.update()
#     |
#     v
# +--------------------------+
# | get()                    |
# | * sync lookup            |
# +--------------------------+
#     |
#     |----> self._sessions.get()
#     |
#     v
# +--------------------------+
# | cleanup_session()        |
# | * stop & deregister      |
# +--------------------------+
#     |
#     |----> remove()
#     |       |
#     |       ----> asyncio.Lock()
#     |       |
#     |       ----> self._sessions.pop()
#     |
#     |----> session.audio_source.stop()
#     |
#     |----> set session.closed = True
#     |
#     v
# +--------------------------+
# | cleanup_all()            |
# | * server shutdown hook   |
# +--------------------------+
#     |
#     |----> list(self._sessions.keys())
#     |
#     |----> [LOOP] -> cleanup_session()
#     |
# [ END ]



import asyncio
import logging
from typing import Dict, Optional

from .livekit_session import LiveKitSession

logger = logging.getLogger("callcenter.livekit.sessions")


class LiveKitSessionManager:


    def __init__(self) -> None:
        self._sessions: Dict[str, LiveKitSession] = {}
        self._lock = asyncio.Lock()

    # ── Registry operations ───────────────────────────────────────────────────

    async def add(self, session: LiveKitSession) -> None:
        """Register a newly created session."""
        async with self._lock:
            self._sessions[session.session_id] = session
        logger.info(
            "[Sessions] + added   session=%s  total=%d",
            session.session_id[:8], len(self._sessions),
        )

    async def remove(self, session_id: str) -> Optional[LiveKitSession]:
        """Remove and return session from registry (does NOT close resources)."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            logger.info(
                "[Sessions] - removed session=%s  total=%d",
                session_id[:8], len(self._sessions),
            )
        return session

    def get(self, session_id: str) -> Optional[LiveKitSession]:
        """Synchronous lookup — returns session or None."""
        return self._sessions.get(session_id)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def cleanup_session(self, session_id: str) -> None:
        
        session = await self.remove(session_id)
        if session is None:
            return   # already removed

        if session.closed:
            return   # already being cleaned up

        session.closed = True

        # Stop the TTS audio pump if it was started
        if session.audio_source is not None:
            try:
                session.audio_source.stop()
            except Exception:
                pass

        logger.info("[Sessions] cleanup done  session=%s", session_id[:8])

    async def cleanup_all(self) -> None:
        """Signal all sessions to close. Called from server shutdown."""
        async with self._lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            await self.cleanup_session(sid)
        logger.info("[Sessions] all sessions cleaned up")

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._sessions)

    @property
    def session_ids(self) -> list:
        return list(self._sessions.keys())


# ── Module singleton ──────────────────────────────────────────────────────────
livekit_session_manager = LiveKitSessionManager()
