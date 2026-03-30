# [ START ]
#     |
#     v
# +--------------------------+
# | <BaseModel> ->           |
# | Field()                  |
# | * default_factory init   |
# +--------------------------+
#     |
#     |----> uuid.uuid4()   * unique ID generation
#     |----> time.time()    * execution timestamping
#     v
# +--------------------------+
# | <BaseModel> ->           |
# | model_dump_json()        |
# | * outbound serialization |
# +--------------------------+
#     |
#     v
# [ YIELD ]

print("[FILE] Entering: schemas.py")
import time
import uuid
from typing import Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# Outbound (FastAPI → Kafka)
# ═══════════════════════════════════════════════════════════════════════════════

class CallRequest(BaseModel):#Represents a new call request
   
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


# ═══════════════════════════════════════════════════════════════════════════════
# GPU Capacity (Worker Service → Scheduler)
# ═══════════════════════════════════════════════════════════════════════════════

class GpuCapacity(BaseModel):
    
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
    
    session_id:  str
    room_id:     str
    node_id:     str
    worker_pid:  int   = 0
    started_at:  float = Field(default_factory=time.time)


class CallCompleted(BaseModel):
   
    session_id:    str
    room_id:       str
    node_id:       str
    duration_sec:  float = 0.0
    completed_at:  float = Field(default_factory=time.time)


class CallFailed(BaseModel):
    
    session_id:   str
    room_id:      str
    node_id:      str
    error:        str   = ""
    retry_count:  int   = 0
    failed_at:    float = Field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════════
# Heartbeat (Worker Service → Scheduler(alive signal))
# ═══════════════════════════════════════════════════════════════════════════════

class WorkerHeartbeat(BaseModel):
    
    node_id:      str
    alive:        bool  = True
    active_calls: int   = 0
    timestamp:    float = Field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════════════════
# DataChannel messages (Scheduler → Browser via LiveKit Server SDK)
# ═══════════════════════════════════════════════════════════════════════════════

class QueueUpdate(BaseModel):
   
    type:      str   = "queue_update"
    position:  int   = 1
    eta_sec:   int   = 120


class CallStart(BaseModel):
  
    type: str = "call_start"