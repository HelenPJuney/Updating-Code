
# [ START ]
#     |
#     v
# +-----------------------------+
# | get_producer()              |
# | * singleton accessor        |
# +-----------------------------+
#     |
#     |----> <CallRequestProducer> -> __init__()
#     |
#     v
# +-----------------------------+
# | <CallRequestProducer> ->    |
# | start()                     |
# | * connect to brokers        |
# +-----------------------------+
#     |
#     |----> <AIOKafkaProducer> -> start()
#     |           |
#     |           ----> [ EXCEPTION ] -> set _fallback = True
#     v
# +-----------------------------+
# | <CallRequestProducer> ->    |
# | submit_call_request()       |
# | * produce to Kafka          |
# +-----------------------------+
#     |
#     |----> [ IF _fallback ] -> return None
#     |           |
#     |           ----> (Trigger local spawn)
#     |
#     |----> <CallRequestRequest> -> model_dump_json()
#     |
#     |----> <AIOKafkaProducer> -> send_and_wait()
#     |           |
#     |           ----> return 0 * queue placeholder
#     v
# +-----------------------------+
# | <CallRequestProducer> ->    |
# | stop()                      |
# | * graceful shutdown         |
# +-----------------------------+
#     |
#     |----> <AIOKafkaProducer> -> stop()
#     |
# [ END ]

import asyncio
import logging
from typing import Optional


from .config import (
    KAFKA_BROKERS,
    KAFKA_SECURITY_PROTOCOL,
    KAFKA_SASL_MECHANISM,
    KAFKA_SASL_USERNAME,
    KAFKA_SASL_PASSWORD,
    KAFKA_PRODUCER_ACKS,
    KAFKA_PRODUCER_RETRIES,
    KAFKA_ENABLE_IDEMPOTENCE,
    KAFKA_REQUEST_TIMEOUT_MS,
    KAFKA_DELIVERY_TIMEOUT_MS,
    TOPIC_CALL_REQUESTS,
)
from .schemas import CallRequest

logger = logging.getLogger("callcenter.kafka.producer")

# ── Optional AIOKafka import ──────────────────────────────────────────────────
try:
    from aiokafka import AIOKafkaProducer
    _AIOKAFKA_AVAILABLE = True
except ImportError:
    _AIOKAFKA_AVAILABLE = False
    logger.warning(
        "[Producer] aiokafka not installed — Kafka produce disabled. "
        "Install with: pip install aiokafka"
    )


class CallRequestProducer:
    """
    AIOKafka producer wrapper with graceful fallback.

    If Kafka is unavailable the system falls back to spawning ai_worker_task
    directly so a missing broker does not break the service in development.
    """

    def __init__(self) -> None:
        self._producer: Optional["AIOKafkaProducer"] = None
        self._started:  bool = False
        self._fallback: bool = False   # True → bypass Kafka, spawn directly

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Connect the Kafka producer.
        Called once at FastAPI startup (lifespan / startup event).
        """
        if not _AIOKAFKA_AVAILABLE:
            logger.warning("[Producer] Running WITHOUT Kafka (aiokafka missing)")
            self._fallback = True
            return

        kwargs: dict = dict(
            bootstrap_servers   = KAFKA_BROKERS,
            acks                = KAFKA_PRODUCER_ACKS,
            enable_idempotence  = KAFKA_ENABLE_IDEMPOTENCE,
            request_timeout_ms  = KAFKA_REQUEST_TIMEOUT_MS,
            value_serializer    = lambda v: v,   # caller passes bytes
            key_serializer      = lambda k: k.encode() if isinstance(k, str) else k,
        )
        if KAFKA_SECURITY_PROTOCOL != "PLAINTEXT":
            kwargs["security_protocol"]    = KAFKA_SECURITY_PROTOCOL
            kwargs["sasl_mechanism"]       = KAFKA_SASL_MECHANISM
            kwargs["sasl_plain_username"]  = KAFKA_SASL_USERNAME
            kwargs["sasl_plain_password"]  = KAFKA_SASL_PASSWORD

        try:
            prod = AIOKafkaProducer(**kwargs)
            await asyncio.wait_for(prod.start(), timeout=10.0)
            self._producer = prod
            logger.info(
                "[Producer] connected to Kafka brokers: %s",
                ", ".join(KAFKA_BROKERS),
            )
        except Exception as exc:
            logger.warning(
                "[Producer] Kafka unavailable (%s) — falling back to direct spawn",
                exc,
            )
            self._fallback = True

        self._started = True

    async def stop(self) -> None:
        """Flush and close producer. Call at FastAPI shutdown."""
        if self._producer:
            try:
                await self._producer.stop()
            except Exception:
                pass
        logger.info("[Producer] stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    async def submit_call_request(self, req: CallRequest) -> Optional[int]:
        """
        Produce a CallRequest message to the call_requests Kafka topic.

        Returns:
            None always — queue position is derived from Kafka lag by the
            Scheduler and communicated back via LiveKit DataChannel
            (queue_update message).  The /livekit/token response returns
            queue_position=null; browsers should poll /livekit/queue-status.

        Fallback:
            Returns None when Kafka is unavailable.  The caller in ai_worker.py
            detects this and spawns ai_worker_task directly.
        """
        if self._fallback or self._producer is None:
            logger.debug(
                "[Producer] fallback: no Kafka — caller must spawn directly  session=%s",
                req.session_id[:8],
            )
            return None   # signal to caller: spawn directly

        payload = req.model_dump_json().encode("utf-8")
        key     = req.session_id.encode("utf-8")

        try:
            await self._producer.send_and_wait(
                TOPIC_CALL_REQUESTS,
                value = payload,
                key   = key,
            )
            logger.info(
                "[Producer] published call_request  session=%s  room=%s",
                req.session_id[:8], req.room_id[:8],
            )
        except Exception as exc:
            logger.exception(
                "[Producer] failed to publish call_request  session=%s  error=%s",
                req.session_id[:8], exc,
            )
            return None   # fallback on produce error

        # Queue position is Kafka-lag-based — return 0 as placeholder.
        # The Scheduler will send the real position via DataChannel.
        return 0

    @property
    def is_kafka_active(self) -> bool:
        return not self._fallback and self._producer is not None


# ── Module-level singleton ────────────────────────────────────────────────────
_producer_instance: Optional[CallRequestProducer] = None


def get_producer() -> CallRequestProducer:
    """Return the module-level singleton producer (created lazily)."""
    global _producer_instance
    if _producer_instance is None:
        _producer_instance = CallRequestProducer()
    return _producer_instance
