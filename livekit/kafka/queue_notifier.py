
# [ START ]
#     |
#     v
# +--------------------------+
# | <QueueNotifier> ->       |
# | start()                  |
# | * init LiveKitAPI client |
# +--------------------------+
#     |
#     |----> <LiveKitAPI> -> __init__()
#     v
# +--------------------------+
# | <QueueNotifier> ->       |
# | broadcast_queue_         |
# | positions()              |
# +--------------------------+
#     |
#     |----> enumerate(queue_items)
#     |           |
#     |           ----> <QueueUpdate> -> model_dump_json()
#     |----> asyncio.gather()
#     |           |
#     |           ----> <QueueNotifier> -> _send_to_room()
#     v
# +--------------------------+
# | <QueueNotifier> ->       |
# | notify_call_starting()   |
# +--------------------------+
#     |
#     |----> <CallStart> -> model_dump_json()
#     |----> <QueueNotifier> -> _send_to_room()
#     v
# +--------------------------+
# | <QueueNotifier> ->       |
# | _send_to_room()          |
# +--------------------------+
#     |
#     |----> <SendDataRequest> -> __init__()
#     |----> <LiveKitAPI> -> room.send_data()
#     v
# +--------------------------+
# | <QueueNotifier> ->       |
# | stop()                   |
# | * close API client       |
# +--------------------------+
#     |
#     |----> <LiveKitAPI> -> aclose()
#     |
# [ END ]


import asyncio
import json
import logging
from typing import Optional

from .config import AVG_CALL_DURATION_SEC
from .schemas import CallRequest, QueueUpdate, CallStart

logger = logging.getLogger("callcenter.kafka.queue_notifier")

# ── livekit-api import (optional) ────────────────────────────────────────────
try:
    from livekit.api import LiveKitAPI
    _LIVEKIT_API_AVAILABLE = True
except ImportError:
    _LIVEKIT_API_AVAILABLE = False
    logger.warning(
        "[Notifier] livekit-api not available — DataChannel notifications disabled. "
        "Install with: pip install livekit-api"
    )


class QueueNotifier:
    """
    Broadcasts queue-position updates and call-start notifications to browsers
    through the LiveKit Server SDK DataChannel (server-side send_data).

    This uses the LiveKit *server* API (not the real-time SDK), so it works
    from any service — the Scheduler does not need to join rooms itself.
    """

    def __init__(self, livekit_url: str, api_key: str, api_secret: str) -> None:
        self._url    = livekit_url
        self._key    = api_key
        self._secret = api_secret
        self._api: Optional["LiveKitAPI"] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not _LIVEKIT_API_AVAILABLE:
            return
        try:
            self._api = LiveKitAPI(self._url, self._key, self._secret)
            logger.info("[Notifier] LiveKit API client ready  url=%s", self._url)
        except Exception as exc:
            logger.warning("[Notifier] failed to create LiveKit API client: %s", exc)
            self._api = None

    async def stop(self) -> None:
        if self._api:
            try:
                await self._api.aclose()
            except Exception:
                pass

    # ── Public API ────────────────────────────────────────────────────────────

    async def broadcast_queue_positions(
        self,
        queue_items: list[CallRequest],
    ) -> None:
        """
        Send a queue_update DataChannel message to every waiting caller.

        queue_items — ordered list (head = position 1) of waiting CallRequests.
        Called by the Scheduler after every enqueue or dequeue operation.
        """
        if not queue_items:
            return

        tasks = []
        for idx, req in enumerate(queue_items):
            position  = idx + 1
            eta_sec   = position * AVG_CALL_DURATION_SEC
            msg       = QueueUpdate(position=position, eta_sec=eta_sec)
            tasks.append(
                self._send_to_room(
                    room_id     = req.room_id,
                    participant = f"user-{req.session_id[:8]}",
                    payload     = msg.model_dump_json().encode("utf-8"),
                )
            )

        # Fan-out concurrently — don't let one slow room block others
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors  = [r for r in results if isinstance(r, Exception)]
        if errors:
            logger.debug("[Notifier] %d DataChannel sends failed", len(errors))

    async def notify_call_starting(self, req: CallRequest) -> None:
        """
        Tell the browser the AI worker is about to join — transition from
        waiting screen to call screen.
        """
        msg = CallStart()
        await self._send_to_room(
            room_id     = req.room_id,
            participant = f"user-{req.session_id[:8]}",
            payload     = msg.model_dump_json().encode("utf-8"),
        )
        logger.info(
            "[Notifier] call_start sent  session=%s  room=%s",
            req.session_id[:8], req.room_id[:8],
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _send_to_room(
        self,
        room_id:     str,
        participant: str,
        payload:     bytes,
    ) -> None:
        if self._api is None:
            logger.debug("[Notifier] skipping DataChannel send (no API client)")
            return

        try:
            from livekit.api import SendDataRequest
            await self._api.room.send_data(
                SendDataRequest(
                    room                   = room_id,
                    data                   = payload,
                    destination_identities = [participant],
                    reliable               = True,
                )
            )
        except Exception as exc:
            logger.debug(
                "[Notifier] DataChannel send failed room=%s participant=%s: %s",
                room_id[:8], participant, exc,
            )
            raise   # re-raise so gather() captures it
