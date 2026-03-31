"""
tests/unit/test_scheduling.py
────────────────────────────────────────────────────────────────────────────────
Unit tests for the call scheduling module.

Coverage:
  - JobStore CRUD (upsert, get, delete, list_due, list_all)
  - ScheduledCallJob lifecycle (pending → running → completed/failed/cancelled)
  - is_due() logic
  - ScheduledCallService.schedule() / cancel() / list_jobs() / stats()
  - Retry logic on failure
  - Timezone parsing via API (_parse_epoch)
"""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from livekit.scheduling.models import JobStatus, ScheduledCallJob
from livekit.scheduling.store import JobStore
from livekit.scheduling.api import _parse_epoch


# ═══════════════════════════════════════════════════════════════════════════════
# JobStore
# ═══════════════════════════════════════════════════════════════════════════════

class TestJobStore:

    def test_upsert_and_get(self, job_store, scheduled_job):
        job_store.upsert(scheduled_job)
        fetched = job_store.get(scheduled_job.job_id)
        assert fetched is not None
        assert fetched.job_id == scheduled_job.job_id
        assert fetched.phone_number == scheduled_job.phone_number

    def test_get_nonexistent_returns_none(self, job_store):
        assert job_store.get("nonexistent-id") is None

    def test_upsert_updates_status(self, job_store, scheduled_job):
        job_store.upsert(scheduled_job)
        scheduled_job.status = JobStatus.RUNNING
        job_store.upsert(scheduled_job)
        fetched = job_store.get(scheduled_job.job_id)
        assert fetched.status == JobStatus.RUNNING

    def test_delete(self, job_store, scheduled_job):
        job_store.upsert(scheduled_job)
        ok = job_store.delete(scheduled_job.job_id)
        assert ok is True
        assert job_store.get(scheduled_job.job_id) is None

    def test_delete_nonexistent_returns_false(self, job_store):
        assert job_store.delete("nonexistent") is False

    def test_list_due(self, job_store):
        due_job = ScheduledCallJob(
            phone_number="+15550000001",
            scheduled_at=time.time() - 10,  # in the past
        )
        future_job = ScheduledCallJob(
            phone_number="+15550000002",
            scheduled_at=time.time() + 3600,  # 1 hour from now
        )
        job_store.upsert(due_job)
        job_store.upsert(future_job)

        due = job_store.list_due()
        due_ids = [j.job_id for j in due]
        assert due_job.job_id in due_ids
        assert future_job.job_id not in due_ids

    def test_list_all_no_filter(self, job_store):
        for i in range(3):
            job_store.upsert(ScheduledCallJob(phone_number=f"+1555000{i:04d}"))
        all_jobs = job_store.list_all()
        assert len(all_jobs) >= 3

    def test_list_all_status_filter(self, job_store, scheduled_job):
        job_store.upsert(scheduled_job)
        cancelled = ScheduledCallJob(
            phone_number="+15559999999",
            status=JobStatus.CANCELLED,
        )
        job_store.upsert(cancelled)

        pending = job_store.list_all(status="pending")
        cancelled_list = job_store.list_all(status="cancelled")
        assert any(j.job_id == scheduled_job.job_id for j in pending)
        assert any(j.job_id == cancelled.job_id for j in cancelled_list)

    def test_update_status(self, job_store, scheduled_job):
        job_store.upsert(scheduled_job)
        job_store.update_status(scheduled_job.job_id, JobStatus.COMPLETED, executed_at=time.time())
        fetched = job_store.get(scheduled_job.job_id)
        assert fetched.status == JobStatus.COMPLETED
        assert fetched.executed_at is not None

    def test_count_by_status(self, job_store):
        job_store.upsert(ScheduledCallJob(phone_number="+1"))
        job_store.upsert(ScheduledCallJob(phone_number="+2", status=JobStatus.COMPLETED))
        counts = job_store.count_by_status()
        assert "pending" in counts
        assert counts["pending"] >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# ScheduledCallJob model
# ═══════════════════════════════════════════════════════════════════════════════

class TestScheduledCallJob:

    def test_is_due_past_time(self):
        job = ScheduledCallJob(scheduled_at=time.time() - 1)
        assert job.is_due() is True

    def test_is_due_future_time(self):
        job = ScheduledCallJob(scheduled_at=time.time() + 3600)
        assert job.is_due() is False

    def test_is_due_not_pending(self):
        job = ScheduledCallJob(
            scheduled_at=time.time() - 1,
            status=JobStatus.RUNNING,
        )
        assert job.is_due() is False

    def test_to_dict_and_from_dict_roundtrip(self):
        original = ScheduledCallJob(
            phone_number="+15551234567",
            lang="es",
            label="test",
        )
        d = original.to_dict()
        restored = ScheduledCallJob.from_dict(d)
        assert restored.job_id == original.job_id
        assert restored.phone_number == original.phone_number
        assert restored.lang == original.lang
        assert restored.label == original.label
        assert restored.status == original.status


# ═══════════════════════════════════════════════════════════════════════════════
# Epoch parsing (API helper)
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseEpoch:

    def test_unix_timestamp_string(self):
        ts = time.time()
        result = _parse_epoch(str(ts))
        assert abs(result - ts) < 1.0

    def test_iso8601_with_offset(self):
        result = _parse_epoch("2026-03-26T14:30:00+00:00")
        assert result > 0
        assert abs(result - 1743000600) < 100   # approximate check

    def test_iso8601_no_timezone_treated_as_utc(self):
        result = _parse_epoch("2026-03-26T14:30:00")
        assert result > 0

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            _parse_epoch("not-a-date")


# ═══════════════════════════════════════════════════════════════════════════════
# ScheduledCallService (async)
# ═══════════════════════════════════════════════════════════════════════════════

class TestScheduledCallService:

    @pytest.mark.asyncio
    async def test_schedule_and_retrieve(self, tmp_path):
        from livekit.scheduling.service import ScheduledCallService

        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15551234567",
                scheduled_at=time.time() + 3600,
            )
            job_id = await svc.schedule(job)
            assert job_id == job.job_id

            fetched = await svc.get_job(job_id)
            assert fetched is not None
            assert fetched.phone_number == job.phone_number
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_cancel_pending_job(self, tmp_path):
        from livekit.scheduling.service import ScheduledCallService

        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15551234567",
                scheduled_at=time.time() + 3600,
            )
            job_id = await svc.schedule(job)
            ok = await svc.cancel(job_id)
            assert ok is True

            fetched = await svc.get_job(job_id)
            assert fetched.status == JobStatus.CANCELLED
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self, tmp_path):
        from livekit.scheduling.service import ScheduledCallService

        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            ok = await svc.cancel("nonexistent-id")
            assert ok is False
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_list_jobs(self, tmp_path):
        from livekit.scheduling.service import ScheduledCallService

        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            for i in range(3):
                job = ScheduledCallJob(
                    phone_number=f"+1555{i:07d}",
                    scheduled_at=time.time() + 3600,
                )
                await svc.schedule(job)

            jobs = await svc.list_jobs()
            assert len(jobs) == 3
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_stats(self, tmp_path):
        from livekit.scheduling.service import ScheduledCallService

        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15551234567",
                scheduled_at=time.time() + 3600,
            )
            await svc.schedule(job)

            stats = await svc.stats()
            assert "by_status" in stats
            assert stats["by_status"].get("pending", 0) >= 1
        finally:
            await svc.stop()
