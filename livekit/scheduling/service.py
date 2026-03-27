# [ START ]
#     |
#     v
# +-----------------------------+
# | Lifecycle Methods           |
# +-----------------------------+
#     |
#     | [ start() ]
#     |----> Open SQLite DB (via executor)
#     |----> Set _running = True
#     |----> Launch _poll_loop() as background Task
#     |
#     | [ stop() ]
#     |----> Set _running = False
#     |----> Cancel _poll_loop() Task
#     |----> Close SQLite DB (via executor)
#     v
# +-----------------------------+
# | Public API Methods          |
# +-----------------------------+
#     |
#     | [ schedule(job) ]
#     |----> Persist job to JobStore.upsert
#     |
#     | [ cancel(job_id) ]
#     |----> Fetch job; CHECK if PENDING
#     |----> Update status to CANCELLED
#     |
#     | [ get_job(job_id) ]
#     |----> Fetch single job from JobStore
#     |
#     | [ list_jobs(...) ]
#     |----> Query JobStore with status/limit/offset filters
#     |
#     | [ stats() ]
#     |----> Aggregate job counts from JobStore
#     v
# +-----------------------------+
# | Core Logic & Polling        |
# +-----------------------------+
#     |
#     | [ _poll_loop() ]
#     |----> WHILE _running:
#     |        ----> await _check_due_jobs()
#     |        ----> await asyncio.sleep(SCHEDULING_POLL_SEC)
#     |
#     | [ _check_due_jobs() ]
#     |----> Query JobStore.list_due()
#     |----> Map due jobs to _execute_job()
#     |----> Throttling: Execute with Semaphore(5)
#     |
#     | [ _execute_job(job) ]
#     |----> Update status to RUNNING
#     |----> TRY:
#     |        ----> _dispatch_call(job)
#     |        ----> Update status to COMPLETED
#     |----> EXCEPT:
#     |        ----> IF retries < max: Reschedule with backoff
#     |        ----> ELSE: Update to FAILED + _send_to_dlq()
#     v
# +-----------------------------+
# | Dispatch & Fallback         |
# +-----------------------------+
#     |
#     | [ _dispatch_call(job) ]
#     |----> IF Kafka active: submit_call_request()
#     |----> ELSE: _sip_outbound_fallback()
#     |
#     | [ _sip_outbound_fallback() ]
#     |----> Direct call to internal SIP module/endpoint
#     |
#     | [ _send_to_dlq(job) ]
#     |----> JSON serialize failure metadata
#     |----> Publish to TOPIC_CALL_DLQ
#     v
# [ YIELD ]

import asyncio
import logging
import os
import time
from typing import List, Optional

from .models import JobStatus, ScheduledCallJob
from .store import JobStore

logger = logging.getLogger("callcenter.scheduling.service")

# Poll interval (seconds): check for due jobs this often
SCHEDULING_POLL_SEC: float = float(os.getenv("SCHEDULING_POLL_SEC", "30"))


class ScheduledCallService:
    """
    Lifecycle:
        await service.start()   # opens DB + starts poll loop
        ...
        await service.stop()    # cancels poll loop + closes DB
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._store   = JobStore(db_path)
        self._running = False
        self._task:   Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self._store.open)
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="scheduling-poll")
        logger.info(
            "[SchedulerSvc] started  poll_interval=%.0fs", SCHEDULING_POLL_SEC
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await asyncio.get_running_loop().run_in_executor(None, self._store.close)
        logger.info("[SchedulerSvc] stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    async def schedule(self, job: ScheduledCallJob) -> str:
        """
        Persist a new scheduled call job.
        Returns the job_id.
        """
        await asyncio.get_running_loop().run_in_executor(
            None, self._store.upsert, job
        )
        logger.info(
            "[SchedulerSvc] job scheduled  job_id=%s  phone=%s  at=%.0f",
            job.job_id[:8], job.phone_number, job.scheduled_at,
        )
        return job.job_id

    async def cancel(self, job_id: str) -> bool:
        """
        Cancel a pending job. Returns False if job not found or not pending.
        """
        loop = asyncio.get_running_loop()
        job  = await loop.run_in_executor(None, self._store.get, job_id)
        if not job or job.status != JobStatus.PENDING:
            return False
        await loop.run_in_executor(
            None, self._store.update_status, job_id, JobStatus.CANCELLED
        )
        logger.info("[SchedulerSvc] job cancelled  job_id=%s", job_id[:8])
        return True

    async def get_job(self, job_id: str) -> Optional[ScheduledCallJob]:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._store.get, job_id
        )

    async def list_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ScheduledCallJob]:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._store.list_all, status, limit, offset
        )

    async def stats(self) -> dict:
        counts = await asyncio.get_running_loop().run_in_executor(
            None, self._store.count_by_status
        )
        return {
            "by_status": counts,
            "total": sum(counts.values()),
            "poll_interval_sec": SCHEDULING_POLL_SEC,
        }

    # ── Poll loop ─────────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Check for due jobs every SCHEDULING_POLL_SEC seconds."""
        while self._running:
            try:
                await self._check_due_jobs()
            except Exception as exc:
                logger.exception("[SchedulerSvc] poll error: %s", exc)
            await asyncio.sleep(SCHEDULING_POLL_SEC)

    async def _check_due_jobs(self) -> None:
        loop = asyncio.get_running_loop()
        due  = await loop.run_in_executor(None, self._store.list_due)
        if not due:
            return
        logger.info("[SchedulerSvc] %d due job(s) found", len(due))
        # Execute concurrently but cap at 5 to avoid overloading Kafka
        sem = asyncio.Semaphore(5)
        async def _bounded(job):
            async with sem:
                await self._execute_job(job)
        await asyncio.gather(*[_bounded(j) for j in due], return_exceptions=True)

    async def _execute_job(self, job: ScheduledCallJob) -> None:
        """
        Execute a single scheduled job:
          1. Mark as RUNNING
          2. Publish SIP outbound call request to Kafka (or fallback)
          3. Mark COMPLETED or retry on failure
        """
        loop = asyncio.get_running_loop()

        # Mark running
        await loop.run_in_executor(
            None, self._store.update_status, job.job_id,
            JobStatus.RUNNING, "", time.time(),
        )

        try:
            await self._dispatch_call(job)
            # Mark completed
            await loop.run_in_executor(
                None, self._store.update_status, job.job_id,
                JobStatus.COMPLETED, "", time.time(),
            )
            logger.info(
                "[SchedulerSvc] job executed  job_id=%s  phone=%s",
                job.job_id[:8], job.phone_number,
            )

        except Exception as exc:
            error_str = str(exc)
            logger.warning(
                "[SchedulerSvc] job failed  job_id=%s  attempt=%d/%d  error=%s",
                job.job_id[:8], job.retry_count + 1, job.max_retries, error_str,
            )

            if job.retry_count + 1 >= job.max_retries:
                # Exhausted retries → FAILED
                await loop.run_in_executor(
                    None, self._store.update_status, job.job_id,
                    JobStatus.FAILED, error_str,
                )
                await self._send_to_dlq(job, error_str)
            else:
                # Reschedule with backoff
                next_ts = time.time() + job.retry_delay * (2 ** job.retry_count)
                job.retry_count  += 1
                job.status        = JobStatus.PENDING
                job.scheduled_at  = next_ts
                job.updated_at    = time.time()
                job.error         = error_str
                await loop.run_in_executor(None, self._store.upsert, job)
                logger.info(
                    "[SchedulerSvc] job rescheduled  job_id=%s  next_at=%.0f",
                    job.job_id[:8], next_ts,
                )

    async def _dispatch_call(self, job: ScheduledCallJob) -> None:
        """
        Publish a CallRequest to Kafka for an outbound scheduled call.
        Falls back to the SIP outbound REST endpoint if Kafka unavailable.
        """
        from ..kafka.producer import get_producer
        from ..kafka.schemas import CallRequest

        req = CallRequest(
            lang         = job.lang,
            llm          = job.llm,
            voice        = job.voice,
            agent_name   = job.agent_name,
            source       = "sip_outbound",
            caller_number= job.phone_number,
            priority     = job.priority,
            scheduled_job_id = job.job_id,
        )

        producer = get_producer()
        if producer.is_kafka_active:
            result = await producer.submit_call_request(req)
            if result is not None:
                return  # success via Kafka
        # Kafka inactive → use SIP outbound REST endpoint
        await self._sip_outbound_fallback(job, req)

    async def _sip_outbound_fallback(self, job: ScheduledCallJob, req) -> None:
        """Call the internal /sip/outbound-call endpoint as fallback."""
        try:
            from ..sip.sip_ingress import sip_session_mgr
            from ..sip.sip_event_handler import _publish_sip_call_request

            success = await _publish_sip_call_request(
                session_id   = req.session_id,
                room_id      = req.room_id,
                caller_number= job.phone_number,
                lang         = job.lang,
                llm          = job.llm,
                voice        = job.voice,
                agent_name   = job.agent_name,
                source       = "sip_outbound",
            )
            if not success:
                raise RuntimeError("SIP outbound dispatch failed")
        except ImportError:
            raise RuntimeError("SIP module not available for outbound fallback")

    async def _send_to_dlq(self, job: ScheduledCallJob, error: str) -> None:
        """Publish exhausted job to Kafka DLQ."""
        try:
            from ..kafka.producer import get_producer
            from ..kafka.config import TOPIC_CALL_DLQ
            import json as _json

            producer = get_producer()
            if not producer.is_kafka_active:
                return
            payload = _json.dumps({
                "type":        "scheduled_job_failed",
                "job_id":      job.job_id,
                "phone_number": job.phone_number,
                "error":       error,
                "retry_count": job.retry_count,
                "scheduled_at": job.scheduled_at,
            }).encode()
            internal = getattr(producer, "_producer", None)
            if internal:
                await internal.send_and_wait(
                    TOPIC_CALL_DLQ,
                    value=payload,
                    key=job.job_id.encode(),
                )
        except Exception as exc:
            logger.debug("[SchedulerSvc] DLQ publish failed: %s", exc)
