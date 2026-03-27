"""
websocket/hub.py
────────────────────────────────────────────────────────────────────────────────
Central asyncio event hub — fan-out broadcast to all subscribed clients.

Design:
  • Each connected WebSocket/SSE client gets an asyncio.Queue (max 200 items).
  • publish() puts a copy of the event dict into every subscriber queue.
  • Slow/disconnected clients: if a queue is full, the oldest event is dropped
    (non-blocking — never stalls the publisher).
  • No external dependencies (pure asyncio).

Event envelope:
  {
    "type":      str,   # call_started | call_completed | call_failed |
                        # queue_update | agent_state | routing_decision |
                        # scheduled_job | system_status | sip_event
    "ts":        float, # UTC epoch
    "session_id": str | null,
    ...                 # event-specific payload
  }
"""

import asyncio
import logging
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set

logger = logging.getLogger("callcenter.websocket.hub")

_MAX_QUEUE_SIZE  = 200    # per subscriber
_HISTORY_SIZE    = 100    # recent events kept for new subscriber catch-up


class EventHub:
    """
    Fan-out broadcast hub.

    Thread-safe within a single asyncio event loop.
    """

    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._history: Deque[Dict] = deque(maxlen=_HISTORY_SIZE)
        self._lock = asyncio.Lock()
        self._total_published: int = 0
        self._total_dropped:   int = 0

    # ── Subscribe / unsubscribe ───────────────────────────────────────────────

    async def subscribe(self, replay_history: bool = True) -> asyncio.Queue:
        """
        Create and register a new subscriber queue.
        If replay_history=True, pre-fill the queue with recent events.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        async with self._lock:
            if replay_history:
                for evt in list(self._history):
                    try:
                        q.put_nowait(evt)
                    except asyncio.QueueFull:
                        break  # history too long for queue capacity
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    # ── Publish ───────────────────────────────────────────────────────────────

    async def publish(self, event: Dict[str, Any]) -> None:
        """
        Broadcast an event to all subscribers.
        Automatically stamps event with 'ts' if missing.
        """
        if "ts" not in event:
            event = {**event, "ts": time.time()}

        async with self._lock:
            self._history.append(event)
            self._total_published += 1
            for q in list(self._subscribers):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop oldest, insert newest (ring-buffer behaviour)
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass
                    self._total_dropped += 1

    # Synchronous publish helper — safe to call from sync context
    def publish_sync(self, event: Dict[str, Any], loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Fire-and-forget publish from synchronous code."""
        try:
            lp = loop or asyncio.get_event_loop()
            if lp.is_running():
                lp.create_task(self.publish(event))
        except Exception:
            pass

    # ── Convenience helpers ───────────────────────────────────────────────────

    async def publish_call_started(self, session_id: str, room_id: str, node_id: str, source: str = "browser") -> None:
        await self.publish({
            "type": "call_started",
            "session_id": session_id,
            "room_id": room_id,
            "node_id": node_id,
            "source": source,
        })

    async def publish_call_completed(self, session_id: str, duration_sec: float, node_id: str) -> None:
        await self.publish({
            "type": "call_completed",
            "session_id": session_id,
            "duration_sec": round(duration_sec, 1),
            "node_id": node_id,
        })

    async def publish_call_failed(self, session_id: str, error: str, retry_count: int = 0) -> None:
        await self.publish({
            "type": "call_failed",
            "session_id": session_id,
            "error": error,
            "retry_count": retry_count,
        })

    async def publish_queue_update(self, queue_depth: int, active_nodes: int) -> None:
        await self.publish({
            "type": "queue_update",
            "queue_depth": queue_depth,
            "active_nodes": active_nodes,
        })

    async def publish_routing_decision(self, session_id: str, rule_name: str, queue_name: str) -> None:
        await self.publish({
            "type": "routing_decision",
            "session_id": session_id,
            "rule_name": rule_name,
            "queue_name": queue_name,
        })

    async def publish_escalation(self, session_id: str, reason: str, agent_id: Optional[str] = None) -> None:
        await self.publish({
            "type": "escalation",
            "session_id": session_id,
            "reason": reason,
            "agent_id": agent_id,
        })

    async def publish_scheduled_job(self, job_id: str, status: str, phone_number: str) -> None:
        await self.publish({
            "type": "scheduled_job",
            "job_id": job_id,
            "status": status,
            "phone_number": phone_number,
        })

    async def publish_system_status(self, online: bool, active_nodes: int, queue_depth: int) -> None:
        await self.publish({
            "type": "system_status",
            "online": online,
            "active_nodes": active_nodes,
            "queue_depth": queue_depth,
        })

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def stats(self) -> Dict:
        return {
            "subscribers":       self.subscriber_count,
            "total_published":   self._total_published,
            "total_dropped":     self._total_dropped,
            "history_size":      len(self._history),
        }
