# [ START: SCHEDULING API CLIENT ]
#       |
#       |--- (A) POST /jobs (Schedule)
#       |--- (B) GET  /jobs (List/Filter)
#       |--- (C) GET  /jobs/{job_id} (Detail)
#       |--- (D) DELETE /jobs/{job_id} (Cancel)
#       v
# +------------------------------------------+
# | scheduling_router -> Endpoint Methods    |
# | * Validate Request (Pydantic/ISO-8601)   |
# +------------------------------------------+
#       |
#       | (If /jobs POST)
#       |----> _parse_epoch(scheduled_at)
#       |      * Convert ISO/Unix to UTC float
#       |      * Check: Is date in future?
#       |
#       |----> [ scheduling_service ]
#       |      |
#       |      |-- schedule(ScheduledCallJob)
#       |      |   * Persist and queue job
#       |      |
#       |      |-- list_jobs(status, limit)
#       |      |   * Fetch job collection
#       |      |
#       |      |-- get_job(job_id)
#       |      |   * Fetch single job
#       |      |
#       |      |-- cancel(job_id)
#       |      |   * Stop/Remove pending job
#       |      |
#       |      `-- stats()
#       |          * Aggregated job counts
#       |
#       | (If Success)              (If Error)
#       v                           v
# +-----------------------+   +-----------------------+
# | Build JSON Response   |   | Log Error             |
# | (Status: scheduled)   |   | Raise 404/422 Error   |
# +-----------------------+   +-----------------------+
#       |
#       v
# [ RETURN API RESPONSE ]


import logging
logger = logging.getLogger(__name__)
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

scheduling_router = APIRouter(prefix="/scheduling", tags=["scheduling"])


# ── Request/response models ───────────────────────────────────────────────────

class ScheduleCallRequest(BaseModel):
    phone_number:  str
    scheduled_at:  str
    # ISO-8601 datetime string, e.g. "2026-03-26T14:30:00+05:30"
    # or Unix timestamp as string "1742995800"
    timezone:      str   = "UTC"
    lang:          str   = "en"
    llm:           str   = "gemini"
    voice:         str   = ""
    agent_name:    str   = "Assistant"
    max_retries:   int   = 3
    retry_delay:   float = 60.0
    label:         str   = ""
    priority:      int   = 0

    @field_validator("scheduled_at")
    @classmethod
    def parse_scheduled_at(cls, v: str) -> str:
        """Validate the datetime string is parseable (returned unchanged)."""
        logger.debug("Executing ScheduleCallRequest.parse_scheduled_at")
        try:
            float(v)   # try as unix timestamp
        except ValueError:
            datetime.fromisoformat(v)  # try as ISO-8601
        return v


def _parse_epoch(dt_str: str) -> float:
    """Convert ISO-8601 or unix-timestamp string to UTC epoch float."""
    logger.debug("Executing _parse_epoch")
    try:
        return float(dt_str)
    except ValueError:
        pass
    # fromisoformat handles offsets in Python 3.11+; fallback for 3.9/3.10
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        raise ValueError(f"Cannot parse datetime: {dt_str!r}")
    if dt.tzinfo is None:
        # Assume UTC when no timezone given
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@scheduling_router.post("/jobs", status_code=201)
async def schedule_job(req: ScheduleCallRequest):
    """Schedule an outbound call for a future time."""
    logger.debug("Executing schedule_job")
    from . import scheduling_service
    from .models import ScheduledCallJob

    try:
        scheduled_epoch = _parse_epoch(req.scheduled_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if scheduled_epoch < time.time():
        raise HTTPException(
            status_code=422,
            detail="scheduled_at must be in the future",
        )

    job = ScheduledCallJob(
        phone_number = req.phone_number,
        lang         = req.lang,
        llm          = req.llm,
        voice        = req.voice,
        agent_name   = req.agent_name,
        scheduled_at = scheduled_epoch,
        timezone     = req.timezone,
        max_retries  = req.max_retries,
        retry_delay  = req.retry_delay,
        label        = req.label,
        priority     = req.priority,
        source       = "api",
    )

    job_id = await scheduling_service.schedule(job)
    return {
        "status":       "scheduled",
        "job_id":       job_id,
        "phone_number": req.phone_number,
        "scheduled_at": scheduled_epoch,
        "scheduled_iso": datetime.fromtimestamp(scheduled_epoch, tz=timezone.utc).isoformat(),
        "timezone":     req.timezone,
        "timestamp":    time.time(),
    }


@scheduling_router.get("/jobs")
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit:  int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List scheduled jobs, optionally filtered by status."""
    logger.debug("Executing list_jobs")
    from . import scheduling_service

    jobs = await scheduling_service.list_jobs(status=status, limit=limit, offset=offset)
    return {
        "jobs":  [j.to_dict() for j in jobs],
        "count": len(jobs),
        "filter_status": status,
        "timestamp": time.time(),
    }


@scheduling_router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """Get a specific scheduled job by ID."""
    logger.debug("Executing get_job")
    from . import scheduling_service

    job = await scheduling_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@scheduling_router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a pending scheduled job."""
    logger.debug("Executing cancel_job")
    from . import scheduling_service

    ok = await scheduling_service.cancel(job_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Job not found or not in pending state",
        )
    return {"status": "cancelled", "job_id": job_id}


@scheduling_router.get("/stats")
async def scheduling_stats():
    """Get job counts by status."""
    logger.debug("Executing scheduling_stats")
    from . import scheduling_service
    stats = await scheduling_service.stats()
    return {**stats, "timestamp": time.time()}
