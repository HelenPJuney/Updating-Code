# [ START ]
#     |
#     v
# +-----------------------------+
# | JobStatus (Enum)            |
# | * State definitions         |
# +-----------------------------+
#     |
#     |----> PENDING
#     |----> RUNNING
#     |----> COMPLETED
#     |----> FAILED
#     |----> CANCELLED
#     v
# +-----------------------------+
# | ScheduledCallJob            |
# | (Dataclass)                 |
# +-----------------------------+
#     |
#     | [ is_due() ]
#     |----> Checks if status is PENDING
#     |----> Compares now >= scheduled_at
#     |
#     | [ to_dict() ]
#     |----> Serializes instance to Dict
#     |----> Converts Enum to string value
#     |
#     | [ from_dict() ]
#     |----> Class method for deserialization
#     |----> Handles default values for missing keys
#     v
# [ YIELD ]

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    PENDING   = "pending"     # waiting for scheduled time
    RUNNING   = "running"     # call in progress
    COMPLETED = "completed"   # call placed successfully
    FAILED    = "failed"      # all retries exhausted
    CANCELLED = "cancelled"   # cancelled by user


@dataclass
class ScheduledCallJob:
    """
    Represents a single scheduled outbound call.

    Stored in SQLite and loaded on startup to survive process restarts.
    """
    # Identity
    job_id:        str   = field(default_factory=lambda: str(uuid.uuid4()))

    # Call parameters
    phone_number:  str   = ""
    lang:          str   = "en"
    llm:           str   = "gemini"
    voice:         str   = ""
    agent_name:    str   = "Assistant"

    # Scheduling
    scheduled_at:  float = field(default_factory=time.time)
    # UTC epoch when the call should be placed
    timezone:      str   = "UTC"
    # IANA timezone string (e.g. "America/New_York") — used for display only;
    # scheduled_at is always stored as UTC epoch

    # Retry
    max_retries:   int   = 3
    retry_count:   int   = 0
    retry_delay:   float = 60.0     # seconds between retries

    # State
    status:        JobStatus = JobStatus.PENDING
    created_at:    float = field(default_factory=time.time)
    updated_at:    float = field(default_factory=time.time)
    executed_at:   Optional[float] = None
    error:         str   = ""

    # Metadata
    label:         str   = ""       # human-readable description
    priority:      int   = 0
    source:        str   = "scheduled"

    def is_due(self, now: Optional[float] = None) -> bool:
        """Return True if the job is pending and its scheduled time has passed."""
        if self.status != JobStatus.PENDING:
            return False
        return (now or time.time()) >= self.scheduled_at

    def to_dict(self) -> dict:
        return {
            "job_id":       self.job_id,
            "phone_number": self.phone_number,
            "lang":         self.lang,
            "llm":          self.llm,
            "voice":        self.voice,
            "agent_name":   self.agent_name,
            "scheduled_at": self.scheduled_at,
            "timezone":     self.timezone,
            "max_retries":  self.max_retries,
            "retry_count":  self.retry_count,
            "retry_delay":  self.retry_delay,
            "status":       self.status.value,
            "created_at":   self.created_at,
            "updated_at":   self.updated_at,
            "executed_at":  self.executed_at,
            "error":        self.error,
            "label":        self.label,
            "priority":     self.priority,
            "source":       self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScheduledCallJob":
        obj = cls(
            job_id       = d["job_id"],
            phone_number = d["phone_number"],
            lang         = d.get("lang", "en"),
            llm          = d.get("llm", "gemini"),
            voice        = d.get("voice", ""),
            agent_name   = d.get("agent_name", "Assistant"),
            scheduled_at = d["scheduled_at"],
            timezone     = d.get("timezone", "UTC"),
            max_retries  = d.get("max_retries", 3),
            retry_count  = d.get("retry_count", 0),
            retry_delay  = d.get("retry_delay", 60.0),
            status       = JobStatus(d.get("status", "pending")),
            created_at   = d.get("created_at", time.time()),
            updated_at   = d.get("updated_at", time.time()),
            executed_at  = d.get("executed_at"),
            error        = d.get("error", ""),
            label        = d.get("label", ""),
            priority     = d.get("priority", 0),
            source       = d.get("source", "scheduled"),
        )
        return obj
