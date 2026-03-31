"""
tests/integration/test_outbound.py
────────────────────────────────────────────────────────────────────────────────
Integration tests for outbound call flows.

Scenarios:
  1. POST /sip/outbound-call → initiate outbound
  2. POST /scheduling/jobs   → schedule + cancel
  3. Scheduled job execution (mock Kafka dispatch)
  4. Retry on dispatch failure
  5. DLQ after max retries
  6. Fallback to direct spawn when Kafka down
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from livekit.kafka.schemas import CallRequest
from livekit.scheduling.models import JobStatus, ScheduledCallJob
from livekit.scheduling.service import ScheduledCallService


# ═══════════════════════════════════════════════════════════════════════════════
# Scheduled outbound via ScheduledCallService
# ═══════════════════════════════════════════════════════════════════════════════

class TestScheduledOutbound:

    @pytest.mark.asyncio
    async def test_job_dispatched_when_due(self, tmp_path):
        """Due job should be dispatched via Kafka."""
        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15550000001",
                scheduled_at=time.time() - 1,   # immediately due
                max_retries=1,
            )
            await svc.schedule(job)

            dispatched = []
            async def mock_dispatch(j):
                dispatched.append(j.job_id)

            with patch.object(svc, "_dispatch_call", side_effect=mock_dispatch):
                await svc._check_due_jobs()

            assert job.job_id in dispatched
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_completed_jobs_not_re_dispatched(self, tmp_path):
        """A completed job should not be picked up again."""
        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15550000002",
                scheduled_at=time.time() - 1,
                status=JobStatus.COMPLETED,
            )
            await svc.schedule(job)

            dispatched = []
            async def mock_dispatch(j):
                dispatched.append(j.job_id)

            with patch.object(svc, "_dispatch_call", side_effect=mock_dispatch):
                await svc._check_due_jobs()

            assert job.job_id not in dispatched
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_failed_dispatch_reschedules_with_backoff(self, tmp_path):
        """On dispatch error, job should be rescheduled (retry_count += 1)."""
        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15550000003",
                scheduled_at=time.time() - 1,
                max_retries=3,
                retry_delay=10.0,
            )
            await svc.schedule(job)

            async def failing_dispatch(j):
                raise RuntimeError("Kafka unavailable")

            with patch.object(svc, "_dispatch_call", side_effect=failing_dispatch):
                await svc._execute_job(job)

            fetched = await svc.get_job(job.job_id)
            assert fetched.retry_count == 1
            assert fetched.status == JobStatus.PENDING
            assert fetched.scheduled_at > time.time()  # pushed to future
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_max_retries_marks_failed_and_dlq(self, tmp_path):
        """After max_retries exhausted, job becomes FAILED."""
        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15550000004",
                scheduled_at=time.time() - 1,
                max_retries=2,
                retry_count=1,   # one retry already done
            )
            await svc.schedule(job)

            dlq_published = []
            async def failing_dispatch(j):
                raise RuntimeError("permanent failure")

            async def mock_dlq(j, err):
                dlq_published.append(j.job_id)

            with patch.object(svc, "_dispatch_call", side_effect=failing_dispatch):
                with patch.object(svc, "_send_to_dlq", side_effect=mock_dlq):
                    await svc._execute_job(job)

            fetched = await svc.get_job(job.job_id)
            assert fetched.status == JobStatus.FAILED
            assert job.job_id in dlq_published
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_kafka_active_dispatch(self, tmp_path):
        """When Kafka is active, job dispatches via producer."""
        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15550000005",
                scheduled_at=time.time() - 1,
            )

            submitted_requests = []
            mock_producer = MagicMock()
            mock_producer.is_kafka_active = True
            mock_producer.submit_call_request = AsyncMock(
                side_effect=lambda req: submitted_requests.append(req) or 0
            )

            with patch("livekit.scheduling.service.get_producer", return_value=mock_producer):
                await svc._dispatch_call(job)

            assert len(submitted_requests) == 1
            assert submitted_requests[0].caller_number == job.phone_number
            assert submitted_requests[0].source == "sip_outbound"
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_cancel_prevents_execution(self, tmp_path):
        """Cancelled jobs should not be dispatched."""
        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15550000006",
                scheduled_at=time.time() - 1,
            )
            job_id = await svc.schedule(job)
            ok = await svc.cancel(job_id)
            assert ok is True

            dispatched = []
            async def mock_dispatch(j):
                dispatched.append(j.job_id)

            with patch.object(svc, "_dispatch_call", side_effect=mock_dispatch):
                await svc._check_due_jobs()

            assert job_id not in dispatched
        finally:
            await svc.stop()


# ═══════════════════════════════════════════════════════════════════════════════
# Routing + outbound integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutboundRouting:

    @pytest.mark.asyncio
    async def test_outbound_source_matches_scheduled_rule(self, routing_engine):
        """Outbound calls should match the scheduled_outbound routing rule."""
        req = CallRequest(
            lang="en",
            source="sip_outbound",
            caller_number="+15551234567",
        )
        decision = await routing_engine.route(req)
        assert decision.rule_name == "scheduled_outbound"
        assert decision.queue_name == "outbound"
        assert decision.fallback_action == "callback"

    @pytest.mark.asyncio
    async def test_outbound_apply_enriches_request(self, routing_engine):
        req = CallRequest(source="sip_outbound", caller_number="+15550000099")
        decision = await routing_engine.route(req)
        decision.apply(req)
        assert req.queue_name == "outbound"
        assert req.routing_rule == "scheduled_outbound"


# ═══════════════════════════════════════════════════════════════════════════════
# Failure scenario: Kafka down on outbound
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutboundKafkaDown:

    @pytest.mark.asyncio
    async def test_fallback_to_sip_outbound_when_kafka_down(self, tmp_path):
        """When Kafka inactive, dispatch falls back to SIP outbound path."""
        svc = ScheduledCallService(str(tmp_path / "test.db"))
        await svc.start()
        try:
            job = ScheduledCallJob(
                phone_number="+15550000099",
                scheduled_at=time.time() - 1,
            )

            sip_fallback_calls = []
            mock_producer = MagicMock()
            mock_producer.is_kafka_active = False  # Kafka is DOWN

            async def mock_sip_fallback(j, req):
                sip_fallback_calls.append(j.phone_number)

            with patch("livekit.scheduling.service.get_producer", return_value=mock_producer):
                with patch.object(svc, "_sip_outbound_fallback", side_effect=mock_sip_fallback):
                    await svc._dispatch_call(job)

            assert job.phone_number in sip_fallback_calls
        finally:
            await svc.stop()
