# [ START: AVAILABILITY CHECK ]
#       |
#       v
# +------------------------------------------+
# | OfflineHandler -> check_status()         |
# | * Return cached status (5s window)       |
# | * Else, trigger _evaluate()              |
# +------------------------------------------+
#       |
#       |----> _evaluate()
#       |      * Read Scheduler node registry
#       |      * Count "alive" nodes (heartbeat)
#       |      * Check total_free slots
#       v
# [ STATUS: ONLINE | OVERLOADED | OFFLINE ]
#       |
#       v
# +------------------------------------------+
# | OfflineHandler -> handle(req, status)    |
# | * ONLINE:     Route normally             |
# | * OVERLOADED: Apply priority_bump        |
# | * OFFLINE:    Trigger _apply_fallback()  |
# +------------------------------------------+
#       |
#       |----> _apply_fallback()
#       |      |
#       |      |-- _handle_voicemail()
#       |      |   * Notify via _notify_browser
#       |      |
#       |      |-- _handle_callback()
#       |      |   * Schedule via scheduling_service
#       |      |
#       |      `-- _handle_ai_bot()
#       |          * Direct spawn ai_worker_task
#       v
# +------------------------------------------+
# | _notify_browser()                        |
# | * Send DataChannel msg via LiveKitAPI    |
# +------------------------------------------+
#       |
#       v
# [ RETURN FallbackResult ]

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("callcenter.offline.handler")

# ── Config ────────────────────────────────────────────────────────────────────
_FALLBACK_ACTION     = os.getenv("OFFLINE_FALLBACK_ACTION", "queue")
# One of: queue | voicemail | callback | ai_bot
_OVERLOAD_PRIORITY   = int(os.getenv("OFFLINE_OVERLOAD_PRIORITY", "8"))
# Priority bump when system is overloaded
_NODE_DEAD_TIMEOUT   = float(os.getenv("SCHEDULER_NODE_DEAD_TIMEOUT_SEC", "30"))


class OfflineStatus(str, Enum):
    ONLINE     = "online"
    OVERLOADED = "overloaded"
    OFFLINE    = "offline"


@dataclass
class FallbackResult:
    status:          OfflineStatus
    action:          str         # "queue" | "voicemail" | "callback" | "ai_bot" | "none"
    priority_bump:   int         # added to req.priority when overloaded
    message:         str         # human-readable explanation
    scheduled_job_id: Optional[str] = None  # set when action == "callback"


class OfflineHandler:
    """
    Stateless handler — reads node state from the Kafka Scheduler singleton
    or from the Kafka WorkerService's last known capacity.
    """

    def __init__(self) -> None:
        logger.debug("Executing OfflineHandler.__init__")
        self._last_check:  float = 0.0
        self._last_status: OfflineStatus = OfflineStatus.ONLINE
        self._check_interval: float = 5.0   # re-evaluate every 5 s

    # ── Public API ────────────────────────────────────────────────────────────

    async def check_status(self) -> OfflineStatus:
        """Return current system availability status (cached 5 s)."""
        logger.debug("Executing OfflineHandler.check_status")
        now = time.time()
        if now - self._last_check < self._check_interval:
            return self._last_status

        status = self._evaluate()
        self._last_status = status
        self._last_check  = now

        if status != OfflineStatus.ONLINE:
            logger.warning("[Offline] system status: %s", status.value)

        return status

    async def handle(self, req, status: Optional[OfflineStatus] = None) -> FallbackResult:
        """
        Determine and apply the fallback action for a call request.

        req: CallRequest (mutated in-place if priority bump applied)
        Returns FallbackResult describing the action taken.
        """
        logger.debug("Executing OfflineHandler.handle")
        if status is None:
            status = await self.check_status()

        if status == OfflineStatus.ONLINE:
            return FallbackResult(
                status       = OfflineStatus.ONLINE,
                action       = "none",
                priority_bump= 0,
                message      = "System online — routing normally",
            )

        if status == OfflineStatus.OVERLOADED:
            # Bump priority so overloaded queue deprioritises new callers
            # relative to VIP callers, but still queues them
            req.priority = max(req.priority, _OVERLOAD_PRIORITY)
            return FallbackResult(
                status       = OfflineStatus.OVERLOADED,
                action       = "queue",
                priority_bump= _OVERLOAD_PRIORITY,
                message      = "System overloaded — queuing with elevated priority",
            )

        # OFFLINE — apply configured fallback
        action = getattr(req, "fallback_action", _FALLBACK_ACTION) or _FALLBACK_ACTION
        return await self._apply_fallback(req, action)

    # ── Node registry reader ──────────────────────────────────────────────────

    def _evaluate(self) -> OfflineStatus:
        """
        Read the in-process Scheduler's node registry if available.
        Falls back to checking the Kafka producer connectivity.
        """
        logger.debug("Executing OfflineHandler._evaluate")
        try:
            from ..kafka.scheduler import _scheduler_instance
            if _scheduler_instance is None:
                # Scheduler not running in-process — assume online
                # (multi-process production: scheduler is a separate service)
                return OfflineStatus.ONLINE

            reg = _scheduler_instance._node_registry
            if not reg:
                return OfflineStatus.OFFLINE

            now = time.time()
            alive = [
                n for n in reg.values()
                if (now - n.last_heartbeat) < _NODE_DEAD_TIMEOUT
            ]
            if not alive:
                return OfflineStatus.OFFLINE

            total_free = sum(n.free_slots for n in alive)
            if total_free == 0:
                return OfflineStatus.OVERLOADED

            return OfflineStatus.ONLINE

        except Exception as exc:
            logger.debug("[Offline] status check error: %s", exc)
            # Conservative: assume online to avoid blocking calls
            return OfflineStatus.ONLINE

    # ── Fallback actions ──────────────────────────────────────────────────────

    async def _apply_fallback(self, req, action: str) -> FallbackResult:
        """Apply a specific fallback action to a call request."""

        logger.debug("Executing OfflineHandler._apply_fallback")
        if action == "voicemail":
            return await self._handle_voicemail(req)
        elif action == "callback":
            return await self._handle_callback(req)
        elif action == "ai_bot":
            return await self._handle_ai_bot(req)
        else:
            # Default: keep in queue
            return FallbackResult(
                status       = OfflineStatus.OFFLINE,
                action       = "queue",
                priority_bump= 0,
                message      = "All nodes offline — call queued in Kafka",
            )

    async def _handle_voicemail(self, req) -> FallbackResult:
        """
        Send a voicemail notification via the DataChannel and mark the call
        as handled.  The actual voicemail recording should be triggered by
        the caller's telephony provider.
        """
        logger.debug("Executing OfflineHandler._handle_voicemail")
        logger.info(
            "[Offline] voicemail fallback  session=%s",
            getattr(req, "session_id", "?")[:8],
        )
        # Publish DataChannel message to the waiting browser
        await self._notify_browser(
            req,
            {
                "type":    "offline_fallback",
                "action":  "voicemail",
                "message": "All agents are currently unavailable. Please leave a voicemail.",
            },
        )
        return FallbackResult(
            status       = OfflineStatus.OFFLINE,
            action       = "voicemail",
            priority_bump= 0,
            message      = "Voicemail notification sent",
        )

    async def _handle_callback(self, req) -> FallbackResult:
        """
        Schedule a callback call for 5 minutes from now using ScheduledCallService.
        """
        logger.debug("Executing OfflineHandler._handle_callback")
        job_id: Optional[str] = None
        try:
            from ..scheduling import scheduling_service
            from ..scheduling.models import ScheduledCallJob

            phone = getattr(req, "caller_number", "") or getattr(req, "room_id", "")
            job   = ScheduledCallJob(
                phone_number = phone,
                lang         = getattr(req, "lang", "en"),
                llm          = getattr(req, "llm", "gemini"),
                voice        = getattr(req, "voice", ""),
                agent_name   = getattr(req, "agent_name", "Assistant"),
                scheduled_at = time.time() + 300,   # 5 minutes
                label        = f"callback for {phone}",
                priority     = max(getattr(req, "priority", 0), 5),
                source       = "offline_callback",
            )
            job_id = await scheduling_service.schedule(job)
            logger.info(
                "[Offline] callback scheduled  job_id=%s  phone=%s",
                job_id[:8], phone,
            )
        except Exception as exc:
            logger.warning("[Offline] callback scheduling failed: %s", exc)

        await self._notify_browser(
            req,
            {
                "type":    "offline_fallback",
                "action":  "callback",
                "message": "All agents busy. We will call you back in ~5 minutes.",
                "job_id":  job_id,
            },
        )
        return FallbackResult(
            status           = OfflineStatus.OFFLINE,
            action           = "callback",
            priority_bump    = 0,
            message          = "Callback scheduled",
            scheduled_job_id = job_id,
        )

    async def _handle_ai_bot(self, req) -> FallbackResult:
        """
        Spawn ai_worker_task directly (bypass Kafka) so the AI handles the
        call even when all GPU nodes are unavailable via the Scheduler.
        This is the existing fallback path — we just trigger it explicitly.
        """
        logger.debug("Executing OfflineHandler._handle_ai_bot")
        try:
            from ..ai_worker import ai_worker_task
            asyncio.ensure_future(
                ai_worker_task(
                    room_id    = req.room_id,
                    session_id = req.session_id,
                    lang       = req.lang,
                    llm_key    = req.llm,
                    voice_stem = req.voice,
                    model_path = req.model_path,
                    agent_name = req.agent_name,
                )
            )
            logger.info(
                "[Offline] AI bot spawned directly  session=%s",
                getattr(req, "session_id", "?")[:8],
            )
            return FallbackResult(
                status       = OfflineStatus.OFFLINE,
                action       = "ai_bot",
                priority_bump= 0,
                message      = "AI bot spawned directly (offline mode)",
            )
        except ImportError:
            logger.error("[Offline] ai_worker_task not importable")
            return FallbackResult(
                status       = OfflineStatus.OFFLINE,
                action       = "queue",
                priority_bump= 0,
                message      = "AI bot unavailable — queued",
            )

    # ── DataChannel notification ──────────────────────────────────────────────

    async def _notify_browser(self, req, message: dict) -> None:
        """Send a DataChannel message to the browser via LiveKit Server SDK."""
        logger.debug("Executing OfflineHandler._notify_browser")
        try:
            from livekit.api import LiveKitAPI, SendDataRequest
            from ..token_service import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
            import json

            room_id = getattr(req, "room_id", None)
            if not room_id:
                return

            async with LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) as api:
                await api.room.send_data(SendDataRequest(
                    room=room_id,
                    data=json.dumps(message).encode("utf-8"),
                    reliable=True,
                ))
        except Exception as exc:
            logger.debug("[Offline] DataChannel notify failed: %s", exc)

    # ── Status helpers ────────────────────────────────────────────────────────

    def get_node_summary(self) -> List[Dict]:
        """Return a summary of known nodes for the health API."""
        logger.debug("Executing OfflineHandler.get_node_summary")
        try:
            from ..kafka.scheduler import _scheduler_instance
            if not _scheduler_instance:
                return []
            now = time.time()
            return [
                {
                    "node_id":      n.node_id,
                    "alive":        (now - n.last_heartbeat) < _NODE_DEAD_TIMEOUT,
                    "active_calls": n.active_calls,
                    "max_calls":    n.max_calls,
                    "free_slots":   n.free_slots,
                    "last_heartbeat": n.last_heartbeat,
                }
                for n in _scheduler_instance._node_registry.values()
            ]
        except Exception:
            return []


# Module-level singleton
offline_handler = OfflineHandler()
