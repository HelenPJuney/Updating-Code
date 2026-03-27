
# [ START ]
#     |
#     v
# +-----------------------------+
# | _main()                     |
# | * process entry point       |
# +-----------------------------+
#     |
#     |----> <WorkerService> -> run()
#     |           |
#     |           ----> <WorkerService> -> start()
#     |           |           |
#     |           |           ----> <AIOKafkaProducer> -> start()
#     |           |           |
#     |           |           ----> <AIOKafkaConsumer> -> start()
#     |           |           |
#     |           |           ----> <AIOKafkaConsumer> -> assign()
#     |           |
#     |           ----> <GpuMonitor> -> start()
#     |           |
#     |           ----> asyncio.TaskGroup()
#     |                       |
#     |                       |----> _consume_calls()
#     |                       |           |
#     |                       |           ----> _run_worker_with_lifecycle()
#     |                       |                       |
#     |                       |                       ----> _publish_started()
#     |                       |                       |
#     |                       |                       ----> ai_worker_task() * AI Pipeline
#     |                       |                       |
#     |                       |                       ----> _publish_completed()
#     |                       |                       |
#     |                       |                       ----> _publish_failed()
#     |                       |                       |
#     |                       |                       ----> _publish_dlq()
#     |                       |
#     |                       |----> _heartbeat_loop()
#     |                                   |
#     |                                   ----> _publish() * WorkerHeartbeat
#     v
# +-----------------------------+
# | <WorkerService> -> stop()    |
# | * drain and shutdown        |
# +-----------------------------+
#     |
#     |----> <GpuMonitor> -> stop()
#     |
#     |----> asyncio.wait_for() * drain active tasks
#     |
#     |----> <AIOKafka> -> stop()
#     |
# [ END ]


import asyncio
import logging
import os
import signal
import sys
import time
from typing import Dict, Optional

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
    KAFKA_PRODUCER_ACKS,
    KAFKA_ENABLE_IDEMPOTENCE,
    KAFKA_PRODUCER_RETRIES,
    KAFKA_REQUEST_TIMEOUT_MS,
    KAFKA_DELIVERY_TIMEOUT_MS,
    TOPIC_CALL_ASSIGNMENTS,      # FIX 2: workers consume from call_assignments
    TOPIC_CALL_STARTED,
    TOPIC_CALL_COMPLETED,
    TOPIC_CALL_FAILED,
    TOPIC_CALL_DLQ,              # FIX 6: dead-letter queue topic
    TOPIC_GPU_CAPACITY,          # FIX 1: correct topic for capacity updates
    TOPIC_WORKER_HEARTBEAT,
    CG_SCHEDULER,
    NODE_ID,
    WORKER_MAX_RETRY,
    WORKER_RETRY_BASE_DELAY,
    WORKER_HEARTBEAT_INTERVAL,
    WORKER_SHUTDOWN_DRAIN_SEC,
)
from .schemas import (
    CallRequest,
    CallStarted,
    CallCompleted,
    CallFailed,
    WorkerHeartbeat,
)
from .gpu_monitor import GpuMonitor
from .token_service_import import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
from .health import (
    metric_active_calls, metric_max_calls,
    metric_gpu_vram_used_mb, metric_gpu_vram_free_mb, metric_gpu_util_pct,
    metric_calls_started, metric_calls_completed, metric_calls_failed,
    metric_call_duration,
)

logger = logging.getLogger("callcenter.kafka.worker_service")

# ── Optional AIOKafka import ──────────────────────────────────────────────────
try:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
    _AIOKAFKA_AVAILABLE = True
except ImportError:
    _AIOKAFKA_AVAILABLE = False
    logger.error("[WorkerSvc] aiokafka not installed — Worker Service cannot start")

# FIX 2: absolute import — stable whether running as a package or a standalone
# process (python -m backend.livekit.kafka.worker_service).
try:
    from backend.livekit.ai_worker import ai_worker_task
    _AI_WORKER_AVAILABLE = True
except ImportError:
    try:
        from ..ai_worker import ai_worker_task   # fallback for editable installs
        _AI_WORKER_AVAILABLE = True
    except ImportError:
        _AI_WORKER_AVAILABLE = False
        logger.error(
            "[WorkerSvc] ai_worker_task not importable — "
            "ensure backend.livekit is on PYTHONPATH"
        )

# Node partition index (set via env var; one value per physical GPU host)
NODE_PARTITION: int = int(os.getenv("NODE_PARTITION", "0"))


# ═══════════════════════════════════════════════════════════════════════════════
# Worker Service
# ═══════════════════════════════════════════════════════════════════════════════

class WorkerService:
    """
    GPU Worker Service — manages a pool of ai_worker_task coroutines.

    Each call is wrapped in _run_worker_with_lifecycle() which handles:
        • startup event publishing
        • retry with exponential backoff
        • completion/failure event publishing
        • session removal from active_tasks

    The GPU monitor publishes capacity updates to Kafka independently.
    """

    def __init__(self) -> None:
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._producer: Optional[AIOKafkaProducer] = None

        # session_id → asyncio.Task
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._shutting_down: bool = False
        self._running:       bool = False

        # GPU monitor
        self._gpu_monitor = GpuMonitor(
            producer        = None,     # producer set after start()
            partition_index = NODE_PARTITION,
        )

        # Start time for duration calculation
        self._task_start_times: Dict[str, float] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not _AIOKAFKA_AVAILABLE:
            raise RuntimeError("aiokafka is not installed")

        # FIX 1: Build producer kwargs cleanly — never touch internal _builder
        # attributes.  SASL params are added conditionally like the consumer.
        prod_kwargs: dict = dict(
            bootstrap_servers   = KAFKA_BROKERS,
            acks                = KAFKA_PRODUCER_ACKS,
            enable_idempotence  = KAFKA_ENABLE_IDEMPOTENCE,
            retries             = KAFKA_PRODUCER_RETRIES,
            request_timeout_ms  = KAFKA_REQUEST_TIMEOUT_MS,
            delivery_timeout_ms = KAFKA_DELIVERY_TIMEOUT_MS,
            value_serializer    = lambda v: v,
            key_serializer      = lambda k: k.encode() if isinstance(k, str) else k,
        )
        if KAFKA_SECURITY_PROTOCOL != "PLAINTEXT":
            prod_kwargs["security_protocol"]   = KAFKA_SECURITY_PROTOCOL
            prod_kwargs["sasl_mechanism"]       = KAFKA_SASL_MECHANISM
            prod_kwargs["sasl_plain_username"]  = KAFKA_SASL_USERNAME
            prod_kwargs["sasl_plain_password"]  = KAFKA_SASL_PASSWORD
        self._producer = AIOKafkaProducer(**prod_kwargs)
        await self._producer.start()

        # Inject producer into GPU monitor
        self._gpu_monitor._producer = self._producer

        # Consumer — pinned to this node's partition for isolation
        consumer_group = f"{CG_SCHEDULER}-worker-{NODE_ID}"
        kwargs: dict = dict(
            bootstrap_servers     = KAFKA_BROKERS,
            group_id              = consumer_group,
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

        self._consumer = AIOKafkaConsumer(**kwargs)
        # Workers only consume calls that the Scheduler has explicitly assigned
        # to this node — raw call_requests are never visible to workers.
        tp = TopicPartition(TOPIC_CALL_ASSIGNMENTS, NODE_PARTITION)
        await self._consumer.start()
        self._consumer.assign([tp])

        self._running = True
        logger.info(
            "[WorkerSvc] started  node=%s  partition=%d",
            NODE_ID, NODE_PARTITION,
        )

    async def stop(self) -> None:
        self._shutting_down = True
        self._running       = False

        # FIX 1: Publish zero-capacity to TOPIC_GPU_CAPACITY (not TOPIC_CALL_REQUESTS)
        if self._gpu_monitor.latest:
            cap = self._gpu_monitor.latest.model_copy(
                update={"max_calls": 0, "free_slots": 0}
            )
            try:
                await self._producer.send_and_wait(
                    TOPIC_GPU_CAPACITY,
                    value = cap.model_dump_json().encode(),
                    key   = NODE_ID.encode(),
                )
            except Exception:
                pass

        self._gpu_monitor.stop()

        # Drain active tasks
        if self._active_tasks:
            logger.info(
                "[WorkerSvc] draining %d active tasks (timeout=%.0fs)",
                len(self._active_tasks), WORKER_SHUTDOWN_DRAIN_SEC,
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_tasks.values(), return_exceptions=True),
                    timeout=WORKER_SHUTDOWN_DRAIN_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[WorkerSvc] drain timeout — cancelling %d tasks",
                    len(self._active_tasks),
                )
                for task in self._active_tasks.values():
                    task.cancel()

        # Stop Kafka clients
        for client in (self._consumer, self._producer):
            if client:
                try:
                    await client.stop()
                except Exception:
                    pass

        logger.info("[WorkerSvc] stopped")

    # ── Main run ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self.start()

        # Start GPU monitor and heartbeat in background
        self._gpu_monitor.start(active_calls_ref=lambda: len(self._active_tasks))

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._consume_calls(),   name="worker-consumer")
                tg.create_task(self._heartbeat_loop(),  name="worker-heartbeat")
        finally:
            await self.stop()

    # ── Consumer loop ─────────────────────────────────────────────────────────

    async def _consume_calls(self) -> None:
        """
        Consume call_requests from this node's assigned partition.

        Only processes messages where:
            msg.key == NODE_ID.encode()
        This is a second layer of safety on top of partition isolation.
        """
        async for msg in self._consumer:
            if not self._running:
                break
            if self._shutting_down:
                break

            try:
                req = CallRequest.model_validate_json(msg.value)

                # Skip messages not intended for this node
                if req.assigned_node and req.assigned_node != NODE_ID:
                    logger.debug(
                        "[WorkerSvc] skipping call meant for node=%s", req.assigned_node
                    )
                    await self._consumer.commit()
                    continue

                # Capacity guard (scheduler should prevent this, but failsafe)
                current_max = (
                    self._gpu_monitor.latest.max_calls
                    if self._gpu_monitor.latest else 99
                )
                if len(self._active_tasks) >= current_max:
                    logger.error(
                        "[WorkerSvc] OVERLOADED max=%d active=%d — rejecting session=%s",
                        current_max, len(self._active_tasks), req.session_id[:8],
                    )
                    await self._publish_failed(req, "Node overloaded", retry_count=req.retry_count)
                    await self._consumer.commit()
                    continue

                # Deduplicate: skip if already running
                if req.session_id in self._active_tasks:
                    logger.debug("[WorkerSvc] duplicate session=%s — skipping", req.session_id[:8])
                    await self._consumer.commit()
                    continue

                # Launch worker task
                task = asyncio.create_task(
                    self._run_worker_with_lifecycle(req),
                    name=f"worker-{req.session_id[:8]}",
                )
                self._active_tasks[req.session_id] = task
                self._task_start_times[req.session_id] = time.time()
                metric_active_calls.labels(node_id=NODE_ID).set(len(self._active_tasks))

                logger.info(
                    "[WorkerSvc] launched worker  session=%s  active=%d",
                    req.session_id[:8], len(self._active_tasks),
                )

                # Commit only after task is safely started
                await self._consumer.commit()

            except Exception as exc:
                logger.exception("[WorkerSvc] consume error: %s", exc)

    # ── Worker lifecycle wrapper ──────────────────────────────────────────────

    async def _run_worker_with_lifecycle(self, req: CallRequest) -> None:
      
        if not _AI_WORKER_AVAILABLE:
            logger.error(
                "[WorkerSvc] ai_worker_task unavailable — rejecting session=%s",
                req.session_id[:8],
            )
            await self._publish_failed(req, "ai_worker_task not importable", retry_count=WORKER_MAX_RETRY)
            await self._publish_dlq(req, "ai_worker_task not importable")
            self._active_tasks.pop(req.session_id, None)
            self._task_start_times.pop(req.session_id, None)
            return

        await self._publish_started(req)
        metric_calls_started.labels(node_id=NODE_ID).inc()
        last_exc: Optional[Exception] = None

        for attempt in range(WORKER_MAX_RETRY):
            if self._shutting_down:
                break
            try:
                # ── THE EXISTING AI PIPELINE — UNCHANGED ───────────────────
                await ai_worker_task(
                    room_id    = req.room_id,
                    session_id = req.session_id,
                    lang       = req.lang,
                    llm_key    = req.llm,
                    voice_stem = req.voice,
                    model_path = req.model_path,
                    agent_name = req.agent_name,
                )
                # Completed successfully
                last_exc = None
                break

            except asyncio.CancelledError:
                logger.info(
                    "[WorkerSvc] task cancelled  session=%s", req.session_id[:8]
                )
                raise   # propagate cancellation

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[WorkerSvc] worker error attempt=%d/%d session=%s: %s",
                    attempt + 1, WORKER_MAX_RETRY, req.session_id[:8], exc,
                )
                if attempt < WORKER_MAX_RETRY - 1:
                    delay = WORKER_RETRY_BASE_DELAY * (2 ** attempt)   # exp. backoff
                    logger.info(
                        "[WorkerSvc] retrying in %.1fs  session=%s",
                        delay, req.session_id[:8],
                    )
                    await asyncio.sleep(delay)

        # Cleanup
        self._active_tasks.pop(req.session_id, None)
        start_time = self._task_start_times.pop(req.session_id, time.time())
        duration   = time.time() - start_time
        metric_active_calls.labels(node_id=NODE_ID).set(len(self._active_tasks))

        if last_exc is None:
            await self._publish_completed(req, duration)
            metric_calls_completed.labels(node_id=NODE_ID).inc()
            metric_call_duration.labels(node_id=NODE_ID).observe(duration)
            logger.info(
                "[WorkerSvc] session complete  session=%s  duration=%.1fs",
                req.session_id[:8], duration,
            )
        else:
            metric_calls_failed.labels(node_id=NODE_ID).inc()
            final_retry_count = req.retry_count + WORKER_MAX_RETRY
            await self._publish_failed(
                req,
                str(last_exc),
                retry_count=final_retry_count,
            )
            # FIX 6: Publish to DLQ after exhausting all retries
            await self._publish_dlq(req, str(last_exc))
            logger.error(
                "[WorkerSvc] session FAILED after %d retries  session=%s  → DLQ",
                WORKER_MAX_RETRY, req.session_id[:8],
            )

    # ── Event publishers ──────────────────────────────────────────────────────

    async def _publish_started(self, req: CallRequest) -> None:
        evt = CallStarted(
            session_id = req.session_id,
            room_id    = req.room_id,
            node_id    = NODE_ID,
            worker_pid = os.getpid(),
        )
        await self._publish(TOPIC_CALL_STARTED, req.session_id, evt.model_dump_json())
        logger.info(
            "[WorkerSvc] published call_started  session=%s", req.session_id[:8]
        )

    async def _publish_completed(self, req: CallRequest, duration: float) -> None:
        evt = CallCompleted(
            session_id   = req.session_id,
            room_id      = req.room_id,
            node_id      = NODE_ID,
            duration_sec = duration,
        )
        await self._publish(TOPIC_CALL_COMPLETED, req.session_id, evt.model_dump_json())

    async def _publish_failed(
        self,
        req: CallRequest,
        error: str,
        retry_count: int = 0,
    ) -> None:
        evt = CallFailed(
            session_id  = req.session_id,
            room_id     = req.room_id,
            node_id     = NODE_ID,
            error       = error,
            retry_count = retry_count,
        )
        await self._publish(TOPIC_CALL_FAILED, req.session_id, evt.model_dump_json())

    async def _publish_dlq(self, req: CallRequest, error: str) -> None:
        """FIX 6: Publish failed call to dead-letter queue for manual inspection."""
        import json as _json
        payload = _json.dumps({
            "session_id":  req.session_id,
            "room_id":     req.room_id,
            "node_id":     NODE_ID,
            "error":       error,
            "retry_count": req.retry_count + WORKER_MAX_RETRY,
            "lang":        req.lang,
            "llm":         req.llm,
            "voice":       req.voice,
            "agent_name":  req.agent_name,
            "original_timestamp": req.timestamp,
        })
        await self._publish(TOPIC_CALL_DLQ, req.session_id, payload)
        logger.warning(
            "[WorkerSvc] DLQ published  session=%s  error=%s",
            req.session_id[:8], error[:120],
        )

    async def _publish(self, topic: str, key: str, value: str) -> None:
        if not self._producer:
            return
        try:
            await self._producer.send_and_wait(
                topic,
                value = value.encode("utf-8"),
                key   = key.encode("utf-8"),
            )
        except Exception as exc:
            logger.warning("[WorkerSvc] publish error topic=%s: %s", topic, exc)

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """
        Publish WorkerHeartbeat to worker_heartbeat topic every WORKER_HEARTBEAT_INTERVAL s.
        The Scheduler uses this as a dead-man's switch to detect node failures.
        """
        while self._running:
            try:
                hb = WorkerHeartbeat(
                    node_id      = NODE_ID,
                    alive        = True,
                    active_calls = len(self._active_tasks),
                )
                await self._publish(
                    TOPIC_WORKER_HEARTBEAT,
                    NODE_ID,
                    hb.model_dump_json(),
                )
                logger.debug(
                    "[WorkerSvc] heartbeat  active=%d", len(self._active_tasks)
                )
            except Exception as exc:
                logger.warning("[WorkerSvc] heartbeat error: %s", exc)
            await asyncio.sleep(WORKER_HEARTBEAT_INTERVAL)


# ── Entry point ───────────────────────────────────────────────────────────────

async def _main() -> None:
    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    service = WorkerService()

    loop = asyncio.get_running_loop()

    def _handle_sigterm(*_):
        logger.info("[WorkerSvc] SIGTERM received — graceful shutdown")
        service._shutting_down = True
        service._running       = False

    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
        loop.add_signal_handler(signal.SIGINT,  _handle_sigterm)
    except (NotImplementedError, AttributeError):
        # Windows does not support add_signal_handler for SIGTERM
        pass

    try:
        await service.run()
    except KeyboardInterrupt:
        pass
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(_main())
