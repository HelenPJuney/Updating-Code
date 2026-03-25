"""
[ START ]
    |
    v
+------------------------+
| _metric()              |
| * initialize metrics   |
+------------------------+
    |
    |----> _NoOpMetric() * fallback if prometheus missing
    |
    v
+------------------------+
| kafka_health()         |
| * GET /health          |
+------------------------+
    |
    |----> get_producer()
    |           |
    |           ----> .is_kafka_active
    v
+------------------------+
| kafka_metrics()        |
| * GET /metrics         |
+------------------------+
    |
    |----> generate_latest()
    |           |
    |           ----> <Prometheus> -> text output
    |----> _Response()
    |
    v
[ YIELD ]
"""
import logging
import time
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger("callcenter.kafka.health")

# ── prometheus-client import (optional) ──────────────────────────────────────
try:
    from prometheus_client import (
        Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST,
    )
    from fastapi.responses import Response as _Response
    _PROM_AVAILABLE = True
except ImportError:
    _PROM_AVAILABLE = False
    logger.warning(
        "[Health] prometheus-client not installed — metrics disabled. "
        "Install with: pip install prometheus-client"
    )


# ── NoOp stubs so the rest of the code doesn't need to guard ─────────────────
class _NoOpMetric:
    def labels(self, **kw):   return self
    def inc(self, v=1):       pass
    def dec(self, v=1):       pass
    def set(self, v):         pass
    def observe(self, v):     pass


def _metric(cls, name, doc, labelnames=()):
    if not _PROM_AVAILABLE:
        return _NoOpMetric()
    try:
        return cls(name, doc, labelnames)
    except Exception:
        return _NoOpMetric()


# ═══════════════════════════════════════════════════════════════════════════════
# Metric definitions
# ═══════════════════════════════════════════════════════════════════════════════

# ── Worker Service (per GPU node) ─────────────────────────────────────────────
metric_active_calls = _metric( #How many people are talking right now on a specific GPU.
    Gauge if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_active_calls",
    "Number of active ai_worker_task coroutines on this node",
    ["node_id"],
)
metric_max_calls = _metric(
    Gauge if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_max_calls",
    "Maximum concurrent calls allowed by GPU capacity",
    ["node_id"],
)
metric_gpu_vram_used_mb = _metric( #How much memory is left on the card.
    Gauge if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_gpu_vram_used_mb",
    "GPU VRAM used in MB",
    ["node_id"],
)
metric_gpu_vram_free_mb = _metric(
    Gauge if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_gpu_vram_free_mb",
    "GPU VRAM free in MB",
    ["node_id"],
)
metric_gpu_util_pct = _metric(
    Gauge if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_gpu_util_pct",
    "GPU compute utilization percentage",
    ["node_id"],
)
metric_calls_started = _metric(
    Counter if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_calls_started_total",
    "Total calls started on this node",
    ["node_id"],
)
metric_calls_completed = _metric(
    Counter if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_calls_completed_total",
    "Total calls completed normally",
    ["node_id"],
)
metric_calls_failed = _metric(
    Counter if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_calls_failed_total",
    "Total calls that failed after all retries",
    ["node_id"],
)
metric_call_duration = _metric(
    Histogram if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_worker_call_duration_seconds",
    "Duration of completed calls in seconds",
    ["node_id"],
)

# ── Scheduler ─────────────────────────────────────────────────────────────────
metric_queue_depth = _metric(  #How many people are currently waiting in the Kafka "lobby
    Gauge if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_scheduler_queue_depth",
    "Number of callers waiting in the FIFO queue",
    [],
)
metric_active_nodes = _metric(
    Gauge if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_scheduler_active_nodes",
    "Number of GPU nodes currently alive",
    [],
)
metric_assignments_total = _metric(
    Counter if _PROM_AVAILABLE else _NoOpMetric,
    "callcenter_scheduler_assignments_total",
    "Total calls assigned to a GPU node",
    [],
)


# ═══════════════════════════════════════════════════════════════════════════════
# FastAPI router
# ═══════════════════════════════════════════════════════════════════════════════

kafka_health_router = APIRouter(prefix="/livekit/kafka", tags=["kafka-health"])


@kafka_health_router.get("/health")
async def kafka_health():
    """
    Simple liveness check for the Kafka integration layer.
    Returns producer and Kafka connectivity status.
    """
    from .producer import get_producer
    producer = get_producer()

    return {
        "status":       "ok",
        "kafka_active": producer.is_kafka_active,
        "node_id":      __import__("socket").gethostname(),
        "timestamp":    time.time(),
    }


@kafka_health_router.get("/metrics")
async def kafka_metrics():
    """
    Expose Prometheus metrics in text format.
    Scrape this endpoint with Prometheus (or use a prometheus-exporter sidecar).
    """
    if not _PROM_AVAILABLE:
        return {"error": "prometheus-client not installed"}
    content = generate_latest()
    return _Response(content=content, media_type=CONTENT_TYPE_LATEST)
