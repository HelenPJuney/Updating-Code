"""
livekit/scheduling
────────────────────────────────────────────────────────────────────────────────
Timezone-aware outbound call scheduler with SQLite persistence.

Usage:
    from livekit.scheduling import scheduling_service, scheduling_router
    app.include_router(scheduling_router)   # /scheduling/jobs

    # Schedule a call:
    job_id = await scheduling_service.schedule(ScheduledCallJob(...))
"""

from .models import ScheduledCallJob, JobStatus
from .service import ScheduledCallService
from .api import scheduling_router

# Module-level singleton
scheduling_service = ScheduledCallService()

__all__ = [
    "scheduling_service", "scheduling_router",
    "ScheduledCallJob", "JobStatus", "ScheduledCallService",
]
