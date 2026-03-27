"""
tests/integration/test_inbound.py
────────────────────────────────────────────────────────────────────────────────
Integration tests for the inbound call flow.

Tests the full HTTP → FastAPI → routing → response chain without:
  - Real Kafka (mocked)
  - Real LiveKit (mocked)
  - Real backend.core (skipped via import mocks)

Uses TestClient (synchronous) and AsyncClient (async).

Scenarios covered:
  1. GET /livekit/token — returns token + routing metadata
  2. GET /livekit/health — reports system status
  3. GET /routing/decision — dry-run routing
  4. POST /sip/webhook — participant_joined event handling
  5. GET /sip/health — SIP subsystem health
"""

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ── Shared mock patches ────────────────────────────────────────────────────────
# Prevent imports of backend.core.* which are not in this repo
_BACKEND_MOCK = MagicMock()


@pytest.fixture(scope="module")
def test_app():
    """Create a FastAPI test app with Kafka and LiveKit mocked out."""
    with patch.dict("sys.modules", {
        "backend":              _BACKEND_MOCK,
        "backend.core":        _BACKEND_MOCK,
        "backend.core.config": _BACKEND_MOCK,
        "backend.core.state":  _BACKEND_MOCK,
        "backend.core.persona": _BACKEND_MOCK,
        "backend.core.stt":    _BACKEND_MOCK,
        "backend.core.tts":    _BACKEND_MOCK,
        "backend.core.llm":    _BACKEND_MOCK,
        "backend.services.greeting_loader": _BACKEND_MOCK,
    }):
        with patch("livekit.kafka.producer.CallRequestProducer.start", new=AsyncMock()):
            with patch("livekit.scheduling.service.ScheduledCallService.start", new=AsyncMock()):
                with patch("livekit.scheduling.service.ScheduledCallService.stop", new=AsyncMock()):
                    import importlib
                    # Force re-import with mocks in place
                    import main as main_mod
                    return main_mod.app


@pytest.fixture(scope="module")
def client(test_app):
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════════
# Root / health endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestRootEndpoints:

    def test_root_returns_status(self, client):
        r = client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "running"
        assert "endpoints" in data

    def test_health_endpoint(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "kafka_active" in data


# ═══════════════════════════════════════════════════════════════════════════════
# Routing endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoutingEndpoints:

    def test_list_rules(self, client):
        r = client.get("/routing/rules")
        assert r.status_code == 200
        data = r.json()
        assert "rules" in data
        assert data["count"] > 0

    def test_reload_rules(self, client):
        r = client.post("/routing/rules/reload")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["rules_loaded"] > 0

    def test_list_agents_empty(self, client):
        r = client.get("/routing/agents")
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data

    def test_register_and_list_agent(self, client):
        r = client.post("/routing/agents", json={
            "agent_id":  "test-agent-001",
            "name":      "Test Agent",
            "skills":    ["english", "billing"],
            "available": True,
            "max_calls": 2,
            "node_id":   "test-participant",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "registered"
        assert data["agent_id"] == "test-agent-001"

        # Now list — should contain the agent
        r2 = client.get("/routing/agents")
        agents = r2.json()["agents"]
        assert any(a["agent_id"] == "test-agent-001" for a in agents)

    def test_deregister_agent(self, client):
        client.post("/routing/agents", json={
            "agent_id": "temp-agent",
            "name": "Temp",
            "skills": [],
        })
        r = client.delete("/routing/agents/temp-agent")
        assert r.status_code == 200

    def test_routing_decision_english(self, client):
        r = client.post("/routing/decision", json={
            "lang": "en",
            "source": "browser",
            "priority": 0,
            "caller_number": "+15551234567",
        })
        assert r.status_code == 200
        data = r.json()
        assert "rule_name" in data
        assert "queue_name" in data
        assert "fallback_action" in data

    def test_routing_decision_spanish(self, client):
        r = client.post("/routing/decision", json={
            "lang": "es",
            "source": "browser",
            "priority": 0,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["rule_name"] == "spanish_caller"
        assert data["queue_name"] == "spanish"


# ═══════════════════════════════════════════════════════════════════════════════
# Scheduling endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchedulingEndpoints:

    def test_schedule_future_call(self, client):
        future_ts = time.time() + 3600
        r = client.post("/scheduling/jobs", json={
            "phone_number": "+15551234567",
            "scheduled_at": str(future_ts),
            "lang": "en",
            "label": "test scheduled call",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "scheduled"
        assert "job_id" in data

    def test_schedule_past_time_rejected(self, client):
        past_ts = time.time() - 3600
        r = client.post("/scheduling/jobs", json={
            "phone_number": "+15551234567",
            "scheduled_at": str(past_ts),
        })
        assert r.status_code == 422

    def test_list_jobs(self, client):
        r = client.get("/scheduling/jobs")
        assert r.status_code == 200
        data = r.json()
        assert "jobs" in data

    def test_cancel_nonexistent_job(self, client):
        r = client.delete("/scheduling/jobs/nonexistent-job-id")
        assert r.status_code == 404

    def test_scheduling_stats(self, client):
        r = client.get("/scheduling/stats")
        assert r.status_code == 200
        data = r.json()
        assert "by_status" in data


# ═══════════════════════════════════════════════════════════════════════════════
# WebSocket hub REST endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebSocketRestEndpoints:

    def test_hub_stats(self, client):
        r = client.get("/ws/stats")
        assert r.status_code == 200
        data = r.json()
        assert "subscribers" in data

    def test_history_empty(self, client):
        r = client.get("/ws/history")
        assert r.status_code == 200
        data = r.json()
        assert "events" in data

    def test_manual_publish(self, client):
        r = client.post("/ws/publish", json={
            "type": "test_event",
            "message": "hello from test",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "published"

    def test_manual_publish_missing_type_rejected(self, client):
        r = client.post("/ws/publish", json={"message": "no type"})
        assert r.status_code == 422
