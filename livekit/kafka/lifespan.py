"""
[ START ]
    |
    v
+------------------------+
| kafka_lifespan()       |
| * app context manager  |
+------------------------+
    |
    |----> start_kafka_producer()
    |           |
    |           ----> <Producer> -> get_producer()
    |           |
    |           ----> <Producer> -> start()
    v
+------------------------+
| yield                  |
| * app running state    |
+------------------------+
    |
    v
+------------------------+
| stop_kafka_producer()  |
| * shutdown cleanup     |
+------------------------+
    |
    |----> <Producer> -> get_producer()
    |           |
    |           ----> <Producer> -> stop()
    |
[ END ]
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger("callcenter.kafka.lifespan")


# ── Standalone startup / shutdown coroutines (for legacy event handlers) ──────

async def start_kafka_producer() -> None:
    """
    Start the Kafka producer singleton.
    Attach to FastAPI startup event or call from your lifespan context.
    """
    from .producer import get_producer
    producer = get_producer()
    await producer.start()
    logger.info("[Lifespan] Kafka producer started  active=%s", producer.is_kafka_active)


async def stop_kafka_producer() -> None:
    """
    Flush and close the Kafka producer singleton.
    Attach to FastAPI shutdown event or call from your lifespan context.
    """
    from .producer import get_producer
    producer = get_producer()
    await producer.stop()
    logger.info("[Lifespan] Kafka producer stopped")


# ── asynccontextmanager lifespan (FastAPI 0.93+) ──────────────────────────────

@asynccontextmanager
async def kafka_lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Use as:
        app = FastAPI(lifespan=kafka_lifespan)
    """
    await start_kafka_producer()
    try:
        yield
    finally:
        await stop_kafka_producer()
