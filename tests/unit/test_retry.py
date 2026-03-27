"""
tests/unit/test_retry.py
────────────────────────────────────────────────────────────────────────────────
Unit tests for retry and fallback logic.

Coverage:
  - WorkerService exponential backoff (mock ai_worker_task)
  - DLQ publishing after max retries
  - OfflineHandler status detection (ONLINE / OVERLOADED / OFFLINE)
  - OfflineHandler fallback result (queue / voicemail / callback / ai_bot)
  - ScheduledCallService retry on dispatch failure
  - EventHub publish / subscribe / fan-out
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from livekit.kafka.schemas import CallRequest


# ═══════════════════════════════════════════════════════════════════════════════
# EventHub
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventHub:

    @pytest.mark.asyncio
    async def test_subscribe_and_receive(self, fresh_hub):
        q = await fresh_hub.subscribe(replay_history=False)
        await fresh_hub.publish({"type": "test_event", "value": 42})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event["type"] == "test_event"
        assert event["value"] == 42
        await fresh_hub.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_fanout_to_multiple_subscribers(self, fresh_hub):
        q1 = await fresh_hub.subscribe(replay_history=False)
        q2 = await fresh_hub.subscribe(replay_history=False)
        await fresh_hub.publish({"type": "fanout_test"})

        e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert e1["type"] == e2["type"] == "fanout_test"

        await fresh_hub.unsubscribe(q1)
        await fresh_hub.unsubscribe(q2)

    @pytest.mark.asyncio
    async def test_history_replay_on_subscribe(self, fresh_hub):
        # Publish before subscribing
        await fresh_hub.publish({"type": "historical_event", "n": 1})
        await fresh_hub.publish({"type": "historical_event", "n": 2})

        q = await fresh_hub.subscribe(replay_history=True)
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert len(events) == 2
        await fresh_hub.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_unsubscribe_stops_receiving(self, fresh_hub):
        q = await fresh_hub.subscribe(replay_history=False)
        await fresh_hub.unsubscribe(q)
        await fresh_hub.publish({"type": "after_unsub"})
        assert q.empty()

    @pytest.mark.asyncio
    async def test_stats(self, fresh_hub):
        q = await fresh_hub.subscribe(replay_history=False)
        await fresh_hub.publish({"type": "stat_test"})
        stats = fresh_hub.stats()
        assert stats["subscribers"] == 1
        assert stats["total_published"] == 1
        await fresh_hub.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_publish_adds_timestamp(self, fresh_hub):
        q = await fresh_hub.subscribe(replay_history=False)
        await fresh_hub.publish({"type": "ts_test"})
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert "ts" in event
        assert event["ts"] > 0
        await fresh_hub.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_convenience_helpers(self, fresh_hub):
        q = await fresh_hub.subscribe(replay_history=False)
        await fresh_hub.publish_call_started("sess-1", "room-1", "node-1")
        event = await asyncio.wait_for(q.get(), timeout=1.0)
        assert event["type"] == "call_started"
        assert event["session_id"] == "sess-1"
        await fresh_hub.unsubscribe(q)


# ═══════════════════════════════════════════════════════════════════════════════
# OfflineHandler
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfflineHandler:

    def _make_handler(self):
        from livekit.offline.handler import OfflineHandler
        h = OfflineHandler()
        h._check_interval = 0  # disable cache
        return h

    def test_evaluate_no_scheduler_returns_online(self):
        handler = self._make_handler()
        with patch("livekit.offline.handler._scheduler_instance", None):
            status = handler._evaluate()
        from livekit.offline.handler import OfflineStatus
        assert status == OfflineStatus.ONLINE

    def test_evaluate_empty_registry_returns_offline(self):
        handler = self._make_handler()
        mock_scheduler = MagicMock()
        mock_scheduler._node_registry = {}
        with patch("livekit.offline.handler._scheduler_instance", mock_scheduler):
            status = handler._evaluate()
        from livekit.offline.handler import OfflineStatus
        assert status == OfflineStatus.OFFLINE

    def test_evaluate_dead_nodes_returns_offline(self):
        handler = self._make_handler()
        dead_node = MagicMock()
        dead_node.last_heartbeat = time.time() - 999   # stale
        dead_node.free_slots = 0
        mock_scheduler = MagicMock()
        mock_scheduler._node_registry = {"n1": dead_node}
        with patch("livekit.offline.handler._scheduler_instance", mock_scheduler):
            status = handler._evaluate()
        from livekit.offline.handler import OfflineStatus
        assert status == OfflineStatus.OFFLINE

    def test_evaluate_no_free_slots_returns_overloaded(self):
        handler = self._make_handler()
        busy_node = MagicMock()
        busy_node.last_heartbeat = time.time()   # alive
        busy_node.free_slots = 0
        mock_scheduler = MagicMock()
        mock_scheduler._node_registry = {"n1": busy_node}
        with patch("livekit.offline.handler._scheduler_instance", mock_scheduler):
            status = handler._evaluate()
        from livekit.offline.handler import OfflineStatus
        assert status == OfflineStatus.OVERLOADED

    def test_evaluate_with_free_slots_returns_online(self):
        handler = self._make_handler()
        alive_node = MagicMock()
        alive_node.last_heartbeat = time.time()
        alive_node.free_slots = 3
        mock_scheduler = MagicMock()
        mock_scheduler._node_registry = {"n1": alive_node}
        with patch("livekit.offline.handler._scheduler_instance", mock_scheduler):
            status = handler._evaluate()
        from livekit.offline.handler import OfflineStatus
        assert status == OfflineStatus.ONLINE

    @pytest.mark.asyncio
    async def test_handle_online_returns_none_action(self):
        from livekit.offline.handler import OfflineHandler, OfflineStatus
        handler = OfflineHandler()
        req = CallRequest()
        result = await handler.handle(req, status=OfflineStatus.ONLINE)
        assert result.action == "none"
        assert result.priority_bump == 0

    @pytest.mark.asyncio
    async def test_handle_overloaded_bumps_priority(self):
        from livekit.offline.handler import OfflineHandler, OfflineStatus
        handler = OfflineHandler()
        req = CallRequest(priority=0)
        result = await handler.handle(req, status=OfflineStatus.OVERLOADED)
        assert result.action == "queue"
        assert req.priority >= 8   # bumped to OFFLINE_OVERLOAD_PRIORITY

    @pytest.mark.asyncio
    async def test_handle_offline_queue_action(self):
        from livekit.offline.handler import OfflineHandler, OfflineStatus
        handler = OfflineHandler()
        req = CallRequest(fallback_action="queue")
        result = await handler.handle(req, status=OfflineStatus.OFFLINE)
        assert result.status == OfflineStatus.OFFLINE
        assert result.action == "queue"


# ═══════════════════════════════════════════════════════════════════════════════
# WorkerService retry logic (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkerRetryLogic:
    """
    Tests for the retry / backoff / DLQ logic in WorkerService.
    We mock the Kafka clients and ai_worker_task.
    """

    def _make_worker_service(self):
        """Create a WorkerService with mocked Kafka clients."""
        import livekit.kafka.worker_service as ws_mod

        svc = ws_mod.WorkerService.__new__(ws_mod.WorkerService)
        svc._consumer        = AsyncMock()
        svc._producer        = AsyncMock()
        svc._active_tasks    = {}
        svc._task_start_times = {}
        svc._shutting_down   = False
        svc._running         = True
        svc._gpu_monitor     = MagicMock()
        svc._gpu_monitor.latest = None

        # Mock _publish to be a no-op
        svc._publish = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        import livekit.kafka.worker_service as ws_mod
        svc = self._make_worker_service()
        req = CallRequest(session_id="test-sess", room_id="test-room")

        with patch.object(ws_mod, "ai_worker_task", new=AsyncMock(return_value=None)):
            with patch.object(ws_mod, "_AI_WORKER_AVAILABLE", True):
                await svc._run_worker_with_lifecycle(req)

        # Should call _publish for started + completed
        calls = [call[0][0] for call in svc._publish.call_args_list]
        assert any("call_started" in c for c in calls)
        assert any("call_completed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_retry_on_failure_then_succeed(self):
        import livekit.kafka.worker_service as ws_mod
        svc = self._make_worker_service()
        req = CallRequest(session_id="retry-sess", room_id="retry-room")

        # Fail twice, succeed on 3rd attempt
        call_count = 0
        async def mock_worker(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient error")

        with patch.object(ws_mod, "ai_worker_task", new=mock_worker):
            with patch.object(ws_mod, "_AI_WORKER_AVAILABLE", True):
                with patch("asyncio.sleep", new=AsyncMock()):
                    await svc._run_worker_with_lifecycle(req)

        assert call_count == 3
        calls = [call[0][0] for call in svc._publish.call_args_list]
        assert any("call_completed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_publishes_dlq_after_max_retries(self):
        import livekit.kafka.worker_service as ws_mod
        from livekit.kafka.config import WORKER_MAX_RETRY

        svc = self._make_worker_service()
        req = CallRequest(session_id="fail-sess", room_id="fail-room")

        async def always_fail(*args, **kwargs):
            raise RuntimeError("permanent failure")

        with patch.object(ws_mod, "ai_worker_task", new=always_fail):
            with patch.object(ws_mod, "_AI_WORKER_AVAILABLE", True):
                with patch("asyncio.sleep", new=AsyncMock()):
                    await svc._run_worker_with_lifecycle(req)

        calls = [call[0][0] for call in svc._publish.call_args_list]
        assert any("call_failed" in c for c in calls)
        assert any("call_dlq" in c for c in calls)
