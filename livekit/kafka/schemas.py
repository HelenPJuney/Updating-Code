"""
[ START ]
    |
    v
+--------------------------+
| <BaseModel> ->           |
| Field()                  |
| * default_factory init   |
+--------------------------+
    |
    |----> uuid.uuid4()   * unique ID generation
    |----> time.time()    * execution timestamping
    v
+--------------------------+
| <BaseModel> ->           |
| model_dump_json()        |
| * outbound serialization |
+--------------------------+
    |
    v
+--------------------------+
| <BaseModel> ->           |
| model_validate_json()    |
| * inbound validation     |
+--------------------------+
    |
    v
[ YIELD ]
"""

import time
import uuid
from typing import Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Outbound (FastAPI → Kafka)
# ═══════════════════════════════════════════════════════════════════════════════

class CallRequest(BaseModel):
    """
    Produced by FastAPI /livekit/token or by the SIP ingress webhook.
    Consumed by the Scheduler and re-emitted to the target node partition.
    """
    schema_version: int   = 1
    session_id:     str   = Field(default_factory=lambda: str(uuid.uuid4()))
    room_id:        str   = Field(default_factory=lambda: str(uuid.uuid4()))
    lang:           str   = "en"
    llm:            str   = "gemini"
    voice:          str   = ""
    model_path:     str   = ""
    agent_name:     str   = ""
    timestamp:      float = Field(default_factory=time.time)
    priority:       int   = 0        # reserved for future VIP lanes
    retry_count:    int   = 0        # incremented on re-schedule after failure
    assigned_node:  Optional[str] = None   # set by Scheduler before forwarding
    source:         str   = "browser"      # "browser" or "sip" — call origin
    caller_number:  str   = ""             # SIP caller phone number / URI


# ═══════════════════════════════════════════════════════════════════════════════
# GPU Capacity (Worker Service → Scheduler)
# ═══════════════════════════════════════════════════════════════════════════════

class GpuCapacity(BaseModel):
    """
    Published by each Worker Service to the gpu_capacity topic every 5 s.
    The Scheduler maintains a live node_registry from these messages.
    """
    node_id:        str
    hostname:       str   = ""
    max_calls:      int   = 0
    active_calls:   int   = 0
    free_slots:     int   = 0      # max_calls - active_calls
    vram_total_mb:  int   = 0
    vram_used_mb:   int   = 0
    vram_free_mb:   int   = 0
    gpu_util_pct:   int   = 0
    per_call_mb:    int   = 0
    timestamp:      float = Field(default_factory=time.time)
    partition_index: int  = 0      # Kafka partition this node listens on


# ═══════════════════════════════════════════════════════════════════════════════
# Lifecycle events (Worker Service → Topics)
# ═══════════════════════════════════════════════════════════════════════════════

class CallStarted(BaseModel):
    """Produced once the Worker Service successfully starts ai_worker_task."""
    session_id:  str
    room_id:     str
    node_id:     str
    worker_pid:  int   = 0
    started_at:  float = Field(default_factory=time.time)


class CallCompleted(BaseModel):
    """Produced when ai_worker_task exits normally (hangup / disconnect)."""
    session_id:    str
    room_id:       str
    node_id:       str
    duration_sec:  float = 0.0
    completed_at:  float = Field(default_factory=time.time)


class CallFailed(BaseModel):
    """Produced when ai_worker_task raises an unrecoverable exception."""
    session_id:   str
    room_id:      str
    node_id:      str
    error:        str   = ""
    retry_count:  int   = 0
    failed_at:    float = Field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════════
# Heartbeat (Worker Service → Scheduler)
# ═══════════════════════════════════════════════════════════════════════════════

class WorkerHeartbeat(BaseModel):
    """Dead-man's switch. Scheduler marks node dead if gap > 30 s."""
    node_id:      str
    alive:        bool  = True
    active_calls: int   = 0
    timestamp:    float = Field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════════
# DataChannel messages (Scheduler → Browser via LiveKit Server SDK)
# ═══════════════════════════════════════════════════════════════════════════════

class QueueUpdate(BaseModel):
    """Sent to browser while caller is in the waiting queue."""
    type:      str   = "queue_update"
    position:  int   = 1
    eta_sec:   int   = 120


class CallStart(BaseModel):
    """Sent to browser immediately before the AI worker joins the room."""
    type: str = "call_start"
