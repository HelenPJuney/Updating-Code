"""
[ START ]
    |
    v
+------------------------+
| pynvml.nvmlInit()      |
| * one-time GPU init    |
+------------------------+
    |
    |----> pynvml.nvmlDeviceGetHandleByIndex()
    |
    v
+------------------------+
| <GpuMonitor> -> start()|
| * initialize loop      |
+------------------------+
    |
    v
+------------------------+
| <GpuMonitor> -> _loop()|
| * async polling task   |
+------------------------+
    |
    |----> <GpuMonitor> -> _publish_once()
    |           |
    |           |----> compute_max_calls()
    |           |           |
    |           |           |----> _per_call_memory_mb()
    |           |           |
    |           |           |----> <pynvml> -> nvmlDeviceGetMemoryInfo()
    |           |           |
    |           |           |----> <pynvml> -> nvmlDeviceGetUtilizationRates()
    |           |
    |           |----> <GpuCapacity> -> model_dump_json()
    |           |
    |           |----> <AIOKafkaProducer> -> send()
    |
    v
+------------------------+
| <GpuMonitor> -> stop() |
| * cancel task          |
+------------------------+
    |
    v
[ END ]
"""
import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Optional

from .config import (
    GPU_INDEX,
    MODEL_MEMORY_MB,
    STT_MODEL,
    LLM_KEY,
    NODE_ID,
    WORKER_GPU_POLL_INTERVAL,
)
from .schemas import GpuCapacity

logger = logging.getLogger("callcenter.kafka.gpu_monitor")

# ── pynvml import + one-time init (FIX 3) ────────────────────────────────────
try:
    import pynvml
    _PYNVML_AVAILABLE = True
except ImportError:
    _PYNVML_AVAILABLE = False
    logger.warning(
        "[GPU] pynvml not installed — GPU monitoring disabled. "
        "Install with: pip install pynvml"
    )

# pynvml.nvmlInit() connects to the NVIDIA driver; calling it repeatedly is
# wasteful and can trigger subtle driver-level warnings on some kernels.
_GPU_HANDLE = None
if _PYNVML_AVAILABLE:
    try:
        pynvml.nvmlInit()
        _GPU_HANDLE = pynvml.nvmlDeviceGetHandleByIndex(GPU_INDEX)
        logger.info(
            "[GPU] NVML initialised  device_index=%d  handle=%s",
            GPU_INDEX, _GPU_HANDLE,
        )
    except Exception as _nvml_err:
        logger.warning(
            "[GPU] NVML init failed (%s) — will use fallback capacity",
            _nvml_err,
        )
        _PYNVML_AVAILABLE = False

# Fallback concurrency when GPU monitoring is unavailable
_FALLBACK_MAX_CALLS: int = int(os.getenv("FALLBACK_MAX_CALLS", "4"))


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class GpuStats:
    """Raw stats from a single pynvml query."""
    vram_total_mb: int
    vram_used_mb:  int
    vram_free_mb:  int
    gpu_util_pct:  int
    max_calls:     int
    per_call_mb:   int


# ─────────────────────────────────────────────────────────────────────────────
def _per_call_memory_mb() -> int:
    """
    Compute per-call VRAM cost from environment-configured model choices.

    Your current setup (Gemini API + Piper) only loads Whisper on the GPU,
    so per-call cost is whisper_medium (1500) + piper (150) = 1650 MB.
    """
    stt_mb = MODEL_MEMORY_MB.get(STT_MODEL, MODEL_MEMORY_MB["whisper_medium"])
    llm_mb = MODEL_MEMORY_MB.get(LLM_KEY,  MODEL_MEMORY_MB["gemini"])   # 0 for API
    tts_mb = MODEL_MEMORY_MB["piper"]
    return stt_mb + llm_mb + tts_mb


def compute_max_calls(gpu_index: int = GPU_INDEX) -> GpuStats:
    """
    Query NVML for current VRAM usage and derive the maximum safe concurrent
    call count.

    FIX 3: uses the module-level cached _GPU_HANDLE — nvmlInit() is NOT
    called here any more.

    Algorithm:
        usable_mb  = vram_free_mb - system_overhead_mb
        max_calls  = usable_mb // per_call_mb

    Backpressure:
        If GPU compute utilization > 90 %, reduce limit by 30 % to prevent
        latency spikes from context-switching overhead.

    Returns GpuStats (contains max_calls).
    Falls back to GpuStats with max_calls=_FALLBACK_MAX_CALLS on any error.
    """
    if not _PYNVML_AVAILABLE or _GPU_HANDLE is None:
        return GpuStats(
            vram_total_mb=0, vram_used_mb=0,
            vram_free_mb=0,  gpu_util_pct=0,
            max_calls=_FALLBACK_MAX_CALLS,
            per_call_mb=_per_call_memory_mb(),
        )

    try:
        # Reuse cached handle — no nvmlInit() here (FIX 3)
        mem_info  = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
        util_info = pynvml.nvmlDeviceGetUtilizationRates(_GPU_HANDLE)

        total_mb = int(mem_info.total // (1024 * 1024))
        used_mb  = int(mem_info.used  // (1024 * 1024))
        free_mb  = int(mem_info.free  // (1024 * 1024))
        util_pct = int(util_info.gpu)

        per_call_mb = _per_call_memory_mb()
        overhead_mb = MODEL_MEMORY_MB["system_overhead"]
        usable_mb   = max(0, free_mb - overhead_mb)

        if per_call_mb > 0:
            max_calls = usable_mb // per_call_mb
        else:
            # No local GPU model (e.g. all API-based) — CPU/RAM limited
            max_calls = _FALLBACK_MAX_CALLS

        # Backpressure: reduce capacity when GPU compute is saturated
        if util_pct > 90:
            max_calls = max(0, int(max_calls * 0.7))
            logger.debug(
                "[GPU] util=%d%% > 90%% — throttled max_calls to %d",
                util_pct, max_calls,
            )

        return GpuStats(
            vram_total_mb=total_mb,
            vram_used_mb=used_mb,
            vram_free_mb=free_mb,
            gpu_util_pct=util_pct,
            max_calls=int(max_calls),
            per_call_mb=per_call_mb,
        )

    except Exception as exc:
        logger.warning("[GPU] pynvml query failed: %s — using fallback", exc)
        return GpuStats(
            vram_total_mb=0, vram_used_mb=0,
            vram_free_mb=0,  gpu_util_pct=0,
            max_calls=_FALLBACK_MAX_CALLS,
            per_call_mb=_per_call_memory_mb(),
        )


# ─────────────────────────────────────────────────────────────────────────────
class GpuMonitor:
    """
    Async polling loop that periodically:
      1. Calls compute_max_calls()
      2. Builds a GpuCapacity Pydantic object
      3. Publishes it to the gpu_capacity Kafka topic
      4. Updates the local capacity reference consumed by WorkerService

    Usage:
        monitor = GpuMonitor(producer=kafka_producer, partition_index=node_partition)
        asyncio.create_task(monitor.run())
        ...
        current_stats = monitor.latest   # always up-to-date
    """

    def __init__(
        self,
        producer,                # AIOKafkaProducer instance
        partition_index: int = 0,
        poll_interval:   float = WORKER_GPU_POLL_INTERVAL,
    ) -> None:
        self._producer       = producer
        self._partition      = partition_index
        self._poll_interval  = poll_interval
        self._running:  bool = False
        self._task: Optional[asyncio.Task] = None
        self.latest: Optional[GpuCapacity] = None

    def start(self, active_calls_ref: "callable") -> None:
        """
        Launch the monitor loop.

        active_calls_ref — zero-argument callable that returns the current
                           active task count from WorkerService; used to
                           compute free_slots accurately.
        """
        self._active_calls_ref = active_calls_ref
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="gpu-monitor")

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._publish_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[GPU] monitor loop error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _publish_once(self) -> None:
        stats        = compute_max_calls()
        active_calls = self._active_calls_ref()
        free_slots   = max(0, stats.max_calls - active_calls)

        cap = GpuCapacity(
            node_id        = NODE_ID,
            hostname       = socket.gethostname(),
            max_calls      = stats.max_calls,
            active_calls   = active_calls,
            free_slots     = free_slots,
            vram_total_mb  = stats.vram_total_mb,
            vram_used_mb   = stats.vram_used_mb,
            vram_free_mb   = stats.vram_free_mb,
            gpu_util_pct   = stats.gpu_util_pct,
            per_call_mb    = stats.per_call_mb,
            partition_index= self._partition,
        )
        self.latest = cap

        # Update Prometheus GPU metrics
        try:
            from .health import (
                metric_max_calls, metric_gpu_vram_used_mb,
                metric_gpu_vram_free_mb, metric_gpu_util_pct,
            )
            metric_max_calls.labels(node_id=NODE_ID).set(stats.max_calls)
            metric_gpu_vram_used_mb.labels(node_id=NODE_ID).set(stats.vram_used_mb)
            metric_gpu_vram_free_mb.labels(node_id=NODE_ID).set(stats.vram_free_mb)
            metric_gpu_util_pct.labels(node_id=NODE_ID).set(stats.gpu_util_pct)
        except Exception:
            pass

        if self._producer is not None:
            try:
                from .config import TOPIC_GPU_CAPACITY
                payload = cap.model_dump_json().encode("utf-8")
                key     = NODE_ID.encode("utf-8")
                await self._producer.send(
                    TOPIC_GPU_CAPACITY,
                    value=payload,
                    key=key,
                )
            except Exception as exc:
                logger.warning("[GPU] failed to publish gpu_capacity: %s", exc)

        logger.debug(
            "[GPU] node=%s  max=%d  active=%d  free=%d  vram_free=%dMB  util=%d%%",
            NODE_ID, stats.max_calls, active_calls, free_slots,
            stats.vram_free_mb, stats.gpu_util_pct,
        )
