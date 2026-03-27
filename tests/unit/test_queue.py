"""
tests/unit/test_queue.py
────────────────────────────────────────────────────────────────────────────────
Unit tests for Kafka schema models and queue management logic.

Coverage:
  - CallRequest serialisation / deserialisation
  - Queue fields (queue_name, priority, required_skills)
  - QueueUpdate / CallStart DataChannel schemas
  - AgentState / EscalationRequest schemas
  - SipSession state machine (RINGING → CONNECTED → COMPLETED/FAILED)
  - SessionManager CRUD
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from livekit.kafka.schemas import (
    AgentState,
    CallCompleted,
    CallFailed,
    CallRequest,
    CallStart,
    CallStarted,
    EscalationRequest,
    EscalationResponse,
    GpuCapacity,
    QueueUpdate,
    WorkerHeartbeat,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CallRequest schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestCallRequestSchema:

    def test_default_values(self, call_request):
        assert call_request.lang == "en"
        assert call_request.llm == "gemini"
        assert call_request.priority == 0
        assert call_request.queue_name == "default"
        assert call_request.required_skills == []
        assert call_request.fallback_action == "queue"
        assert call_request.source == "browser"
        assert call_request.ai_assist_mode is False
        assert call_request.scheduled_job_id is None

    def test_auto_generated_ids(self, call_request):
        import uuid
        uuid.UUID(call_request.session_id)   # should not raise
        uuid.UUID(call_request.room_id)

    def test_json_roundtrip(self, call_request):
        json_str = call_request.model_dump_json()
        restored = CallRequest.model_validate_json(json_str)
        assert restored.session_id == call_request.session_id
        assert restored.room_id    == call_request.room_id
        assert restored.lang       == call_request.lang

    def test_routing_fields_serialised(self):
        req = CallRequest(
            lang="es",
            required_skills=["spanish", "billing"],
            queue_name="spanish",
            routing_rule="spanish_caller",
            priority=5,
            fallback_action="voicemail",
        )
        json_str = req.model_dump_json()
        restored = CallRequest.model_validate_json(json_str)
        assert "spanish" in restored.required_skills
        assert restored.queue_name == "spanish"
        assert restored.fallback_action == "voicemail"

    def test_ai_assist_mode(self):
        req = CallRequest(ai_assist_mode=True, escalation_target="agent-123")
        assert req.ai_assist_mode is True
        assert req.escalation_target == "agent-123"

    def test_scheduled_job_id_field(self):
        req = CallRequest(scheduled_job_id="job-abc-123")
        assert req.scheduled_job_id == "job-abc-123"


# ═══════════════════════════════════════════════════════════════════════════════
# Lifecycle event schemas
# ═══════════════════════════════════════════════════════════════════════════════

class TestLifecycleSchemas:

    def test_call_started(self):
        evt = CallStarted(session_id="s1", room_id="r1", node_id="n1", worker_pid=1234)
        j = CallStarted.model_validate_json(evt.model_dump_json())
        assert j.session_id == "s1"
        assert j.worker_pid == 1234

    def test_call_completed_duration(self):
        evt = CallCompleted(session_id="s1", room_id="r1", node_id="n1", duration_sec=142.5)
        j = CallCompleted.model_validate_json(evt.model_dump_json())
        assert abs(j.duration_sec - 142.5) < 0.01

    def test_call_failed_error(self):
        evt = CallFailed(session_id="s1", room_id="r1", node_id="n1",
                         error="connection refused", retry_count=3)
        j = CallFailed.model_validate_json(evt.model_dump_json())
        assert j.error == "connection refused"
        assert j.retry_count == 3


# ═══════════════════════════════════════════════════════════════════════════════
# GPU capacity schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestGpuCapacitySchema:

    def test_free_slots(self):
        cap = GpuCapacity(
            node_id="node-1",
            max_calls=4,
            active_calls=2,
            free_slots=2,
            vram_total_mb=8000,
            vram_used_mb=3000,
            vram_free_mb=5000,
            gpu_util_pct=45,
            partition_index=0,
        )
        j = GpuCapacity.model_validate_json(cap.model_dump_json())
        assert j.free_slots == 2
        assert j.gpu_util_pct == 45


# ═══════════════════════════════════════════════════════════════════════════════
# DataChannel message schemas
# ═══════════════════════════════════════════════════════════════════════════════

class TestDataChannelSchemas:

    def test_queue_update(self):
        msg = QueueUpdate(position=3, eta_sec=180)
        assert msg.type == "queue_update"
        assert msg.position == 3
        j = QueueUpdate.model_validate_json(msg.model_dump_json())
        assert j.eta_sec == 180

    def test_call_start(self):
        msg = CallStart()
        assert msg.type == "call_start"


# ═══════════════════════════════════════════════════════════════════════════════
# Agent State schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentStateSchema:

    def test_default_values(self):
        state = AgentState(agent_id="a1")
        assert state.available is True
        assert state.max_calls == 1
        assert state.skills == []

    def test_with_skills(self):
        state = AgentState(
            agent_id="a1",
            name="Alice",
            skills=["billing", "english"],
            max_calls=3,
            active_calls=1,
        )
        j = AgentState.model_validate_json(state.model_dump_json())
        assert "billing" in j.skills
        assert j.max_calls == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Escalation schemas
# ═══════════════════════════════════════════════════════════════════════════════

class TestEscalationSchemas:

    def test_escalation_request(self):
        req = EscalationRequest(
            session_id="s1",
            room_id="r1",
            reason="user_request",
            required_skills=["billing"],
            transcript_summary="Customer is upset about billing.",
        )
        j = EscalationRequest.model_validate_json(req.model_dump_json())
        assert j.reason == "user_request"
        assert "billing" in j.required_skills

    def test_escalation_response_accepted(self):
        resp = EscalationResponse(
            accepted=True,
            agent_id="agent-001",
            agent_name="Alice",
            eta_sec=30,
        )
        j = EscalationResponse.model_validate_json(resp.model_dump_json())
        assert j.accepted is True
        assert j.agent_name == "Alice"


# ═══════════════════════════════════════════════════════════════════════════════
# SIP Session state machine
# ═══════════════════════════════════════════════════════════════════════════════

class TestSipSessionStateMachine:

    @pytest.mark.asyncio
    async def test_register_creates_ringing_session(self):
        from livekit.sip.sip_session_manager import SipSessionManager, SipCallState
        mgr = SipSessionManager()
        sess = await mgr.register("call-1", "sess-1", "room-1", "+15551234567")
        assert sess.state == SipCallState.RINGING
        assert sess.caller_number == "+15551234567"

    @pytest.mark.asyncio
    async def test_mark_connected(self):
        from livekit.sip.sip_session_manager import SipSessionManager, SipCallState
        mgr = SipSessionManager()
        await mgr.register("call-1", "sess-1", "room-1")
        sess = await mgr.mark_connected("sess-1")
        assert sess.state == SipCallState.CONNECTED

    @pytest.mark.asyncio
    async def test_mark_completed(self):
        from livekit.sip.sip_session_manager import SipSessionManager, SipCallState
        mgr = SipSessionManager()
        await mgr.register("call-1", "sess-1", "room-1")
        await mgr.mark_connected("sess-1")
        sess = await mgr.mark_completed("sess-1")
        assert sess.state == SipCallState.COMPLETED

    @pytest.mark.asyncio
    async def test_mark_failed(self):
        from livekit.sip.sip_session_manager import SipSessionManager, SipCallState
        mgr = SipSessionManager()
        await mgr.register("call-1", "sess-1", "room-1")
        sess = await mgr.mark_failed("sess-1")
        assert sess.state == SipCallState.FAILED

    @pytest.mark.asyncio
    async def test_remove_clears_all_indices(self):
        from livekit.sip.sip_session_manager import SipSessionManager
        mgr = SipSessionManager()
        await mgr.register("call-1", "sess-1", "room-1")
        await mgr.remove("sess-1")
        assert mgr.get_by_session("sess-1") is None
        assert mgr.get_by_room("room-1") is None
        assert mgr.get_by_sip_call("call-1") is None

    @pytest.mark.asyncio
    async def test_active_count(self):
        from livekit.sip.sip_session_manager import SipSessionManager
        mgr = SipSessionManager()
        await mgr.register("c1", "s1", "r1")
        await mgr.register("c2", "s2", "r2")
        assert mgr.active_count == 2
        await mgr.mark_completed("s1")
        assert mgr.active_count == 1

    @pytest.mark.asyncio
    async def test_idempotent_register(self):
        from livekit.sip.sip_session_manager import SipSessionManager
        mgr = SipSessionManager()
        sess1 = await mgr.register("call-1", "sess-1", "room-1")
        sess2 = await mgr.register("call-1", "sess-X", "room-X")   # same sip_call_id
        assert sess1.session_id == sess2.session_id   # returns existing
        assert mgr.total_count == 1
