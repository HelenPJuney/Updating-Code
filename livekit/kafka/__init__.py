"""
backend/livekit/kafka/__init__.py
──────────────────────────────────────────────────────────────────────────────
Kafka-based call scheduling and GPU-aware scaling layer.

Sub-modules:
    config          — Kafka broker / topic / node configuration (Redis-free)
    schemas         — Pydantic message models (CallRequest, GpuCapacity, …)
    producer        — FastAPI-side AIOKafka producer (used by ai_worker.py)
    scheduler       — Call Scheduler Service (standalone process)
    worker_service  — GPU Worker Service (standalone process, one per node)
    gpu_monitor     — pynvml GPU capacity computation
    queue_notifier  — LiveKit DataChannel queue-position broadcasts
"""

from .producer import CallRequestProducer, get_producer   # noqa: F401

__all__ = ["CallRequestProducer", "get_producer"]
