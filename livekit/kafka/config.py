[ START ]
    |
    v
+--------------------------+
| TOPIC_CALL_REQUESTS      |
| * inbound call entry     |
+--------------------------+
    |
    |----> CG_SCHEDULER * Consumer Group
    |
    v
+--------------------------+
| TOPIC_CALL_ASSIGNMENTS   |
| * task distribution      |
+--------------------------+
    |
    |----> NODE_ID * identifies worker
    |
    v
+--------------------------+
| TOPIC_WORKER_HEARTBEAT   |
| * health check status    |
+--------------------------+
    |
    |----> TOPIC_GPU_CAPACITY * monitor VRAM
    |
    v
+--------------------------+
| TOPIC_CALL_STARTED       |
| * active call signal     |
+--------------------------+
    |
    |----> TOPIC_CALL_COMPLETED * success
    |
    |----> TOPIC_CALL_FAILED    * error
    |
    |----> TOPIC_CALL_DLQ       * exhausted retries
    |
[ END ]


import os
import socket

# ── Kafka ─────────────────────────────────────────────────────────────────────
KAFKA_BROKERS: list[str] = os.getenv("KAFKA_BROKERS", "localhost:9092").split(",")
KAFKA_SECURITY_PROTOCOL: str = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
KAFKA_SASL_MECHANISM: str = os.getenv("KAFKA_SASL_MECHANISM", "PLAIN")
KAFKA_SASL_USERNAME: str = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD: str = os.getenv("KAFKA_SASL_PASSWORD", "")

# Producer reliability settings
KAFKA_PRODUCER_ACKS: str = "all"           # wait for all in-sync replicas
KAFKA_PRODUCER_RETRIES: int = 10
KAFKA_ENABLE_IDEMPOTENCE: bool = True
KAFKA_REQUEST_TIMEOUT_MS: int = 30_000
KAFKA_DELIVERY_TIMEOUT_MS: int = 120_000

# Consumer reliability settings
KAFKA_AUTO_OFFSET_RESET: str = "earliest"
KAFKA_ENABLE_AUTO_COMMIT: bool = False     # manual commit for exactly-once
KAFKA_MAX_POLL_INTERVAL_MS: int = 300_000
KAFKA_SESSION_TIMEOUT_MS: int = 30_000
KAFKA_HEARTBEAT_INTERVAL_MS: int = 10_000

# ── Topic names ───────────────────────────────────────────────────────────────
TOPIC_CALL_REQUESTS:    str = "call_requests"     # FastAPI   → Scheduler (inbound)Where new calls land
TOPIC_CALL_ASSIGNMENTS: str = "call_assignments"  # Scheduler → Worker Service (outbound)
TOPIC_CALL_WAIT_QUEUE:  str = "call_wait_queue"   # (reserved — no longer actively used)
TOPIC_GPU_CAPACITY:     str = "gpu_capacity"
TOPIC_CALL_STARTED:     str = "call_started"
TOPIC_CALL_COMPLETED:   str = "call_completed"
TOPIC_CALL_FAILED:      str = "call_failed"
TOPIC_WORKER_HEARTBEAT: str = "worker_heartbeat"
TOPIC_CALL_DLQ:         str = "call_dlq"          # Dead-letter queue for exhausted retries
TOPIC_SIP_EVENTS:       str = "sip_events"         # SIP call lifecycle events (optional)

# Consumer group names
CG_SCHEDULER:  str = "scheduler-group"
CG_ANALYTICS:  str = "analytics-group"
CG_DLQ:        str = "dlq-group"
CG_SIP:        str = "sip-group"                   # SIP event consumers

# ── Node identity ─────────────────────────────────────────────────────────────
NODE_ID: str = os.getenv("NODE_ID", socket.gethostname())

# ── GPU ───────────────────────────────────────────────────────────────────────
GPU_INDEX: int = int(os.getenv("GPU_INDEX", "0"))

# Per-model VRAM footprints in MB — adjust via env vars if needed
MODEL_MEMORY_MB: dict[str, int] = { #how much "weight" each model has.
    "whisper_tiny":       600,
    "whisper_base":       800,
    "whisper_medium":    1_500,
    "whisper_large":     2_800,
    "whisper_large_v3":  3_200,
    "qwen_7b":           6_000,
    "qwen_14b":         10_000,
    "gemini":                0,   # API-based, no local VRAM
    "piper":               150,   # Piper TTS is tiny
    "system_overhead": int(os.getenv("MODEL_MEMORY_OVERHEAD_MB", "2048")),
}

STT_MODEL: str = os.getenv("STT_MODEL", "whisper_medium")
LLM_KEY:   str = os.getenv("LLM_KEY",   "gemini")

# ── Scheduler behaviour ───────────────────────────────────────────────────────
SCHEDULER_NODE_DEAD_TIMEOUT_SEC: int   = 30    # mark node dead after N seconds with no heartbeat
SCHEDULER_QUEUE_BROADCAST_SEC:   float = 5.0   # how often to re-broadcast queue positions

# ── Worker Service behaviour ──────────────────────────────────────────────────
WORKER_MAX_RETRY:           int   = 3     # max ai_worker_task restarts per call
WORKER_RETRY_BASE_DELAY:    float = 2.0   # seconds (doubled each retry)
WORKER_HEARTBEAT_INTERVAL:  float = 10.0  # seconds between heartbeat publishes
WORKER_GPU_POLL_INTERVAL:   float = 5.0   # seconds between gpu_capacity publishes
WORKER_SHUTDOWN_DRAIN_SEC:  float = 60.0  # wait this long for active tasks to finish

# ── Average call duration estimate (for ETA display) ─────────────────────────
AVG_CALL_DURATION_SEC: int = 120    # used purely for queue ETA estimate

# ── Wait queue capacity (Kafka-lag-based overflow guard) ─────────────────────
# If Kafka consumer lag on call_requests exceeds this value the scheduler
# pauses its consumer — new messages accumulate in Kafka naturally (backpressure).
MAX_QUEUE_SIZE: int = int(os.getenv("MAX_QUEUE_SIZE", "10000"))
