"""
[ START ]
    |
    v
+-----------------------------+
| _main()                     |
| * global entry point        |
+-----------------------------+
    |
    |----> <CallScheduler> -> run()
    |           |
    |           ----> <CallScheduler> -> start()
    |           |           |
    |           |           ----> <AIOKafkaConsumer> -> start()
    |           |           |
    |           |           ----> <AIOKafkaProducer> -> start()
    |           |           |
    |           |           ----> <QueueNotifier> -> start()
    |           |
    |           ----> asyncio.TaskGroup()
    |                       |
    |                       |----> _consume_call_requests()
    |                       |           |
    |                       |           ----> _select_best_node()
    |                       |           |
    |                       |           ----> _assign_call()
    |                       |           |
    |                       |           ----> <AIOKafkaConsumer> -> pause()
    |                       |
    |                       |----> _consume_events()
    |                       |           |
    |                       |           ----> _on_gpu_capacity()
    |                       |           |
    |                       |           ----> _on_heartbeat()
    |                       |           |
    |                       |           ----> _on_call_finished()
    |                       |           |
    |                       |           ----> _resume_paused_partitions()
    |                       |
    |                       |----> _dead_node_watchdog()
    |                       |
    |                       |----> _periodic_queue_broadcast()
    |                                   |
    |                                   ----> _refresh_lag_cache()
    |                                   |
    |                                   ----> <QueueNotifier> -> 
    |                                         broadcast_queue_positions()
    v
+-----------------------------+
| <CallScheduler> -> stop()   |
| * cleanup and shutdown      |
+-----------------------------+
    |
    |----> <AIOKafka> -> stop()
    |
    |----> <QueueNotifier> -> stop()
    |
[ END ]
"""

import asyncio
import logging
import time
from collections import deque
from typing import Deque, Dict, Optional, Set


from .config import (
    KAFKA_BROKERS,
    KAFKA_SECURITY_PROTOCOL,
    KAFKA_SASL_MECHANISM,
    KAFKA_SASL_USERNAME,
    KAFKA_SASL_PASSWORD,
    KAFKA_AUTO_OFFSET_RESET,
    KAFKA_ENABLE_AUTO_COMMIT,
    KAFKA_MAX_POLL_INTERVAL_MS,
    KAFKA_SESSION_TIMEOUT_MS,
    KAFKA_HEARTBEAT_INTERVAL_MS,
    TOPIC_CALL_REQUESTS,
    TOPIC_CALL_ASSIGNMENTS,
    TOPIC_GPU_CAPACITY,
    TOPIC_CALL_COMPLETED,
    TOPIC_CALL_FAILED,
    TOPIC_WORKER_HEARTBEAT,
    CG_SCHEDULER,
    NODE_ID,
    SCHEDULER_NODE_DEAD_TIMEOUT_SEC,
    SCHEDULER_QUEUE_BROADCAST_SEC,
    AVG_CALL_DURATION_SEC,
    MAX_QUEUE_SIZE,
)
from .schemas import (
    CallRequest,
    GpuCapacity,
    CallCompleted,
    CallFailed,
    WorkerHeartbeat,
)
from .queue_notifier import QueueNotifier
from .token_service_import import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
from .health import metric_queue_depth, metric_active_nodes, metric_assignments_total

logger = logging.getLogger("callcenter.kafka.scheduler")

# ── Optional aiokafka import ──────────────────────────────────────────────────
try:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
    _AIOKAFKA_AVAILABLE = True
except ImportError:
    _AIOKAFKA_AVAILABLE = False
    logger.error("[Scheduler] aiokafka not installed — Scheduler cannot start")


# ═══════════════════════════════════════════════════════════════════════════════
# Node registry entry
# ═══════════════════════════════════════════════════════════════════════════════

class NodeState:
    """Live in-memory view of a single GPU node's capacity."""

    __slots__ = (
        "node_id", "max_calls", "active_calls", "free_slots",
        "partition_index", "last_heartbeat", "last_capacity_ts",
        "active_sessions",     # set[session_id] — tracks in-flight calls
    )

    def __init__(self, cap: GpuCapacity) -> None:
        self.node_id          = cap.node_id
        self.max_calls        = cap.max_calls
        self.active_calls     = cap.active_calls
        self.free_slots       = cap.free_slots
        self.partition_index  = cap.partition_index
        self.last_heartbeat   = time.time()
        self.last_capacity_ts = cap.timestamp
        self.active_sessions: Set[str] = set()

    def update(self, cap: GpuCapacity) -> None:
        self.max_calls        = cap.max_calls
        self.active_calls     = cap.active_calls
        self.free_slots       = cap.free_slots
        self.partition_index  = cap.partition_index
        self.last_capacity_ts = cap.timestamp
        self.last_heartbeat   = time.time()

    @property
    def is_alive(self) -> bool:
        return time.time() - self.last_heartbeat < SCHEDULER_NODE_DEAD_TIMEOUT_SEC


# ═══════════════════════════════════════════════════════════════════════════════
# Scheduler
# ═══════════════════════════════════════════════════════════════════════════════

class CallScheduler:
    """
    Main Scheduler — Kafka-only, no Redis.

    State lives entirely in self._node_registry (in-memory).
    Kafka consumer pause/resume acts as the backpressure mechanism instead
    of a Redis FIFO list.
    """

    def __init__(self) -> None:
        self._node_registry: Dict[str, NodeState] = {}

        # Kafka clients
        self._consumer_requests: Optional["AIOKafkaConsumer"] = None
        self._consumer_events:   Optional["AIOKafkaConsumer"] = None
        self._producer:          Optional["AIOKafkaProducer"] = None

        # Pause/resume state — set of TopicPartitions currently paused
        self._paused_partitions: Set["TopicPartition"] = set()

        # FIX 1+2: ordered deque of full CallRequest objects waiting for a GPU
        # slot. Storing the full object (not just session_id) ensures the real
        # room_id is available when broadcasting queue positions to LiveKit rooms.
        # Appended when a call cannot be assigned; removed when assigned.
        self._pending_sessions: Deque[CallRequest] = deque()

        # NOTE: _pending_count is REMOVED (FIX 3).
        # Queue depth is always computed as len(self._pending_sessions) to
        # prevent the counter from drifting out of sync with the deque.

        # FIX 4: cached Kafka lag — computed periodically, not per-request.
        # /queue-status reads _cached_lag directly instead of creating a consumer.
        self._cached_lag: int = 0
        self._lag_cache_ts: float = 0.0

        # LiveKit DataChannel notifier
        self._notifier = QueueNotifier(
            livekit_url = LIVEKIT_URL,
            api_key     = LIVEKIT_API_KEY,
            api_secret  = LIVEKIT_API_SECRET,
        )

        self._running: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not _AIOKAFKA_AVAILABLE:
            raise RuntimeError("aiokafka not installed")

        # Shared consumer kwargs factory
        def _ck(group_suffix: str = "") -> dict:
            gid = CG_SCHEDULER if not group_suffix else f"{CG_SCHEDULER}-{group_suffix}"
            kwargs: dict = dict(
                bootstrap_servers     = KAFKA_BROKERS,
                group_id              = gid,
                auto_offset_reset     = KAFKA_AUTO_OFFSET_RESET,
                enable_auto_commit    = KAFKA_ENABLE_AUTO_COMMIT,
                max_poll_interval_ms  = KAFKA_MAX_POLL_INTERVAL_MS,
                session_timeout_ms    = KAFKA_SESSION_TIMEOUT_MS,
                heartbeat_interval_ms = KAFKA_HEARTBEAT_INTERVAL_MS,
            )
            if KAFKA_SECURITY_PROTOCOL != "PLAINTEXT":
                kwargs["security_protocol"]   = KAFKA_SECURITY_PROTOCOL
                kwargs["sasl_mechanism"]      = KAFKA_SASL_MECHANISM
                kwargs["sasl_plain_username"] = KAFKA_SASL_USERNAME
                kwargs["sasl_plain_password"] = KAFKA_SASL_PASSWORD
            return kwargs

        # Consumer 1: call_requests — uses the shared scheduler group so that
        # Kafka partitions are assigned to exactly one replica (leader-by-partition).
        self._consumer_requests = AIOKafkaConsumer(
            TOPIC_CALL_REQUESTS,
            **_ck(),
        )

        # Consumer 2: worker events (capacity, completed, failed, heartbeat)
        self._consumer_events = AIOKafkaConsumer(
            TOPIC_GPU_CAPACITY,
            TOPIC_CALL_COMPLETED,
            TOPIC_CALL_FAILED,
            TOPIC_WORKER_HEARTBEAT,
            **_ck("events"),
        )

        # Producer: emit assignments to call_assignments topic
        prod_kwargs: dict = dict(
            bootstrap_servers  = KAFKA_BROKERS,
            value_serializer   = lambda v: v,
            key_serializer     = lambda k: k.encode() if isinstance(k, str) else k,
            enable_idempotence = True,
            acks               = "all",
        )
        if KAFKA_SECURITY_PROTOCOL != "PLAINTEXT":
            prod_kwargs["security_protocol"]   = KAFKA_SECURITY_PROTOCOL
            prod_kwargs["sasl_mechanism"]      = KAFKA_SASL_MECHANISM
            prod_kwargs["sasl_plain_username"] = KAFKA_SASL_USERNAME
            prod_kwargs["sasl_plain_password"] = KAFKA_SASL_PASSWORD

        self._producer = AIOKafkaProducer(**prod_kwargs)

        await self._consumer_requests.start()
        await self._consumer_events.start()
        await self._producer.start()
        await self._notifier.start()

        self._running = True
        logger.info("[Scheduler] started  node=%s", NODE_ID)

    async def stop(self) -> None:
        self._running = False
        for client in (
            self._consumer_requests,
            self._consumer_events,
            self._producer,
        ):
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass
        await self._notifier.stop()
        logger.info("[Scheduler] stopped")

    # ── Main run ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self.start()
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._consume_call_requests(),  name="sched-req")
                tg.create_task(self._consume_events(),         name="sched-events")
                tg.create_task(self._dead_node_watchdog(),     name="sched-watchdog")
                tg.create_task(self._periodic_queue_broadcast(), name="sched-broadcast")
        finally:
            await self.stop()

    # ── Consumer: call_requests ───────────────────────────────────────────────

    async def _consume_call_requests(self) -> None:
        """
        Consume call_requests.

        Kafka consumer group assignment guarantees exactly-one partition
        ownership per Scheduler replica — no leader lock needed.

        Backpressure: when all nodes are full, partitions are paused so Kafka
        buffers messages broker-side.  They resume when capacity frees up.

        FIX 5: Session IDs are tracked in _pending_sessions deque so that
        the periodic broadcaster can send accurate per-session positions.
        """
        async for msg in self._consumer_requests:
            if not self._running:
                break
            try:
                req = CallRequest.model_validate_json(msg.value)

                node = self._select_best_node()
                if node:
                    await self._assign_call(req, node)
                    await self._consumer_requests.commit()
                else:
                    # No capacity — pause this partition so Kafka buffers
                    # the message broker-side.  Do NOT commit the offset;
                    # the message will be re-delivered after resume.

                    # FIX 1+2: append the full CallRequest so broadcast has
                    # the correct room_id for DataChannel targeting.
                    if not any(r.session_id == req.session_id for r in self._pending_sessions):
                        self._pending_sessions.append(req)

                    # FIX 3: use len() — no separate counter
                    tp = TopicPartition(msg.topic, msg.partition)
                    if tp not in self._paused_partitions:
                        self._consumer_requests.pause(tp)
                        self._paused_partitions.add(tp)
                        logger.info(
                            "[Scheduler] no capacity — paused partition %s:%d  pending=%d",
                            msg.topic, msg.partition, len(self._pending_sessions),
                        )
                    if len(self._pending_sessions) >= MAX_QUEUE_SIZE:
                        logger.warning(
                            "[Scheduler] Queue backlog=%d exceeds MAX_QUEUE_SIZE=%d",
                            len(self._pending_sessions), MAX_QUEUE_SIZE,
                        )

            except Exception as exc:
                logger.exception("[Scheduler] error processing call_request: %s", exc)
                try:
                    await self._consumer_requests.commit()
                except Exception:
                    pass

    # ── Consumer: worker events ───────────────────────────────────────────────

    async def _consume_events(self) -> None:
        """
        Process gpu_capacity, call_completed, call_failed, worker_heartbeat.
        All events update in-memory node state; no Redis interaction.
        """
        async for msg in self._consumer_events:
            if not self._running:
                break
            try:
                topic = msg.topic

                if topic == TOPIC_GPU_CAPACITY:
                    cap = GpuCapacity.model_validate_json(msg.value)
                    await self._on_gpu_capacity(cap)

                elif topic == TOPIC_WORKER_HEARTBEAT:
                    hb = WorkerHeartbeat.model_validate_json(msg.value)
                    self._on_heartbeat(hb)

                elif topic == TOPIC_CALL_COMPLETED:
                    evt = CallCompleted.model_validate_json(msg.value)
                    self._on_call_finished(evt.node_id, evt.session_id)
                    logger.info(
                        "[Scheduler] call_completed  session=%s  node=%s  duration=%.0fs",
                        evt.session_id[:8], evt.node_id, evt.duration_sec,
                    )
                    # Capacity freed — resume any paused partitions
                    self._resume_paused_partitions()

                elif topic == TOPIC_CALL_FAILED:
                    evt = CallFailed.model_validate_json(msg.value)
                    self._on_call_finished(evt.node_id, evt.session_id)
                    logger.warning(
                        "[Scheduler] call_failed  session=%s  node=%s  error=%s",
                        evt.session_id[:8], evt.node_id, evt.error[:80],
                    )
                    # Capacity freed — resume any paused partitions
                    self._resume_paused_partitions()

                await self._consumer_events.commit()

            except Exception as exc:
                logger.exception(
                    "[Scheduler] error processing %s: %s", msg.topic, exc
                )

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_gpu_capacity(self, cap: GpuCapacity) -> None:
        """Update or register node in the in-memory registry."""
        if cap.node_id in self._node_registry:
            self._node_registry[cap.node_id].update(cap)
        else:
            self._node_registry[cap.node_id] = NodeState(cap)
            logger.info("[Scheduler] new node registered  node=%s", cap.node_id)

    def _on_call_finished(self, node_id: str, session_id: str) -> None:
        """
        Decrement active_calls and increment free_slots for the node.
        Called for both call_completed and call_failed.
        Pure in-memory — no Redis.
        """
        node = self._node_registry.get(node_id)
        if node:
            node.active_sessions.discard(session_id)
            node.active_calls = max(0, node.active_calls - 1)
            node.free_slots   = max(0, node.free_slots + 1)

    def _on_heartbeat(self, hb: WorkerHeartbeat) -> None:
        """Refresh last_heartbeat timestamp for the node."""
        if hb.node_id in self._node_registry:
            self._node_registry[hb.node_id].last_heartbeat = time.time()
        logger.debug(
            "[Scheduler] heartbeat  node=%s  active=%d",
            hb.node_id, hb.active_calls,
        )

    # ── Assignment logic ──────────────────────────────────────────────────────

    def _select_best_node(self) -> Optional[NodeState]:
        """Best-fit bin-packing: node with most free slots (alive)."""
        candidates = [
            n for n in self._node_registry.values()
            if n.free_slots > 0 and n.is_alive
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda n: n.free_slots)

    async def _assign_call(self, req: CallRequest, node: NodeState) -> None:
        """
        Assign a call to a GPU node.

        1. Pop the session from _pending_sessions if it was queued (FIX 5).
        2. Update in-memory node state immediately (no Redis).
        3. Produce assignment to call_assignments topic (node's partition).
        4. Notify browser via LiveKit DataChannel.
        """
        req.assigned_node = node.node_id

        # FIX 4: Remove matching CallRequest from the pending deque by scanning
        # for session_id.  Using .remove(req) would compare full objects; scanning
        # by session_id is safe and explicit regardless of object equality rules.
        for r in list(self._pending_sessions):
            if r.session_id == req.session_id:
                self._pending_sessions.remove(r)
                break   # only one entry per session_id

        # Update in-memory state
        node.active_calls += 1
        node.free_slots    = max(0, node.free_slots - 1)
        node.active_sessions.add(req.session_id)

        # Update Prometheus scheduler metrics
        metric_assignments_total.inc()
        metric_active_nodes.set(len([n for n in self._node_registry.values() if n.is_alive]))

        # Produce to call_assignments — Worker Service consumes from here
        if self._producer:
            payload = req.model_dump_json().encode("utf-8")
            await self._producer.send_and_wait(
                TOPIC_CALL_ASSIGNMENTS,
                value    = payload,
                key      = node.node_id.encode("utf-8"),
                partition= node.partition_index,
            )

        logger.info(
            "[Scheduler] assigned  session=%s → node=%s  (free_slots=%d)",
            req.session_id[:8], node.node_id, node.free_slots,
        )

        # Notify browser: call is about to start
        await self._notifier.notify_call_starting(req)

    # ── Pause / resume Kafka partitions ───────────────────────────────────────

    def _resume_paused_partitions(self) -> None:
        """
        Resume all paused call_requests partitions when any capacity frees up.
        The consumer will re-deliver the last un-committed message immediately.
        """
        if not self._paused_partitions or self._consumer_requests is None:
            return
        if self._select_best_node() is None:
            return   # still no capacity — stay paused

        self._consumer_requests.resume(*self._paused_partitions)
        logger.info(
            "[Scheduler] resumed %d paused partition(s)",
            len(self._paused_partitions),
        )
        self._paused_partitions.clear()

    # ── Queue position via Kafka lag (FIX 4) ─────────────────────────────────

    async def _refresh_lag_cache(self) -> None:
        """
        FIX 4: Compute Kafka consumer lag once per broadcast interval and
        store it in self._cached_lag.  The /queue-status endpoint reads this
        cached value — it never spins up a new consumer per request.
        """
        # FIX 7: use len(self._pending_sessions) as the floor — no _pending_count
        pending = len(self._pending_sessions)
        if self._consumer_requests is None:
            self._cached_lag = pending
            return
        try:
            assignment = self._consumer_requests.assignment()
            if not assignment:
                self._cached_lag = pending
                return
            end_offsets = await self._consumer_requests.end_offsets(list(assignment))
            lag = sum(
                max(0, end_off - self._consumer_requests.position(tp))
                for tp, end_off in end_offsets.items()
            )
            # FIX 7: floor is len(pending_sessions), not a possibly-drifted counter
            self._cached_lag = max(lag, pending)
            self._lag_cache_ts = time.time()
        except Exception:
            self._cached_lag = pending

    async def _get_kafka_lag(self) -> int:
        """Return the most recent cached Kafka lag value."""
        return self._cached_lag

    # ── Periodic tasks ────────────────────────────────────────────────────────

    async def _dead_node_watchdog(self) -> None:
        """Detect dead nodes and evict them from the registry."""
        while self._running:
            await asyncio.sleep(SCHEDULER_NODE_DEAD_TIMEOUT_SEC)
            dead_nodes = [
                n for n in list(self._node_registry.values())
                if not n.is_alive
            ]
            for node in dead_nodes:
                logger.warning(
                    "[Scheduler] node dead — evicting  node=%s  "
                    "in-flight sessions=%d  pending_queue=%d",
                    node.node_id, len(node.active_sessions),
                    len(self._pending_sessions),  # FIX 6: use len(), not _pending_count
                )
                # In-flight sessions on this dead node will produce call_failed
                # events when the WorkerService restarts — no extra recovery needed.
                del self._node_registry[node.node_id]
            if dead_nodes:
                self._resume_paused_partitions()

    async def _periodic_queue_broadcast(self) -> None:
        """
        FIX 4: Refresh cached Kafka lag each interval.
        FIX 5: Broadcast accurate per-session queue positions using the
               _pending_sessions deque so waiting browsers get correct ETAs.
        """
        while self._running:
            await asyncio.sleep(SCHEDULER_QUEUE_BROADCAST_SEC)
            try:
                # FIX 4: update cached lag
                await self._refresh_lag_cache()

                # FIX 5: broadcast real CallRequest objects — correct room_ids
                # FIX 3: use len() directly — no _pending_count
                metric_queue_depth.set(len(self._pending_sessions))
                metric_active_nodes.set(len([n for n in self._node_registry.values() if n.is_alive]))
                if self._pending_sessions:
                    # FIX 5: pass the actual queued CallRequest objects.
                    # room_id is now the genuine room (not a session_id placeholder)
                    # so DataChannel messages reach the correct LiveKit rooms.
                    queue_items: list[CallRequest] = list(self._pending_sessions)
                    await self._notifier.broadcast_queue_positions(queue_items)
                    logger.debug(
                        "[Scheduler] queue broadcast lag=%d  sessions=%d",
                        self._cached_lag, len(self._pending_sessions),
                    )
            except Exception as exc:
                logger.debug("[Scheduler] periodic broadcast error: %s", exc)


# ── Kafka consumer kwargs factory ─────────────────────────────────────────────

def _kafka_consumer_kwargs(group_id: str) -> dict:
    kwargs: dict = dict(
        bootstrap_servers      = KAFKA_BROKERS,
        group_id               = group_id,
        auto_offset_reset      = KAFKA_AUTO_OFFSET_RESET,
        enable_auto_commit     = KAFKA_ENABLE_AUTO_COMMIT,
        max_poll_interval_ms   = KAFKA_MAX_POLL_INTERVAL_MS,
        session_timeout_ms     = KAFKA_SESSION_TIMEOUT_MS,
        heartbeat_interval_ms  = KAFKA_HEARTBEAT_INTERVAL_MS,
    )
    if KAFKA_SECURITY_PROTOCOL != "PLAINTEXT":
        kwargs["security_protocol"]   = KAFKA_SECURITY_PROTOCOL
        kwargs["sasl_mechanism"]      = KAFKA_SASL_MECHANISM
        kwargs["sasl_plain_username"] = KAFKA_SASL_USERNAME
        kwargs["sasl_plain_password"] = KAFKA_SASL_PASSWORD
    return kwargs


# ── Module-level singleton (used when scheduler runs in-process) ──────────────

# FIX 4: when the Scheduler and FastAPI share the same process (e.g. dev mode)
# this singleton allows /queue-status to read the cached lag without any
# Kafka I/O per request.
_scheduler_instance: Optional[CallScheduler] = None


def get_cached_lag() -> int:
    """Return the most recent queue lag cached by the running Scheduler.
    Returns 0 when the Scheduler runs out-of-process (multi-process prod deploy)."""
    if _scheduler_instance is not None:
        return _scheduler_instance._cached_lag
    return 0


# ── Entry point ───────────────────────────────────────────────────────────────

async def _main() -> None:
    global _scheduler_instance
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    scheduler = CallScheduler()
    _scheduler_instance = scheduler
    try:
        await scheduler.run()
    except KeyboardInterrupt:
        pass
    finally:
        await scheduler.stop()
        _scheduler_instance = None


if __name__ == "__main__":
    import asyncio as _asyncio
    _asyncio.run(_main())
