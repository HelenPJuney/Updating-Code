"""
tests/conftest.py
────────────────────────────────────────────────────────────────────────────────
Shared pytest fixtures.

Provides:
  - call_request: a minimal CallRequest with all defaults
  - routing_engine: a fresh RoutingEngine with default rules loaded
  - job_store: an in-memory SQLite JobStore
  - event_hub: a fresh EventHub
"""

import asyncio
import sys
from pathlib import Path

import pytest

# ── Make livekit package importable ─────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ── Event loop ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── CallRequest fixture ───────────────────────────────────────────────────────
@pytest.fixture
def call_request():
    from livekit.kafka.schemas import CallRequest
    return CallRequest(
        lang="en",
        llm="gemini",
        voice="",
        agent_name="Test Agent",
        source="browser",
        caller_number="+15551234567",
        priority=0,
    )


@pytest.fixture
def sip_call_request():
    from livekit.kafka.schemas import CallRequest
    return CallRequest(
        lang="en",
        llm="gemini",
        source="sip",
        caller_number="+15559876543",
        priority=0,
    )


@pytest.fixture
def vip_call_request():
    from livekit.kafka.schemas import CallRequest
    return CallRequest(
        lang="en",
        llm="gemini",
        source="browser",
        caller_number="+18005551234",
        priority=10,
    )


# ── RoutingEngine fixture ─────────────────────────────────────────────────────
@pytest.fixture
def routing_engine():
    from livekit.routing.engine import RoutingEngine
    engine = RoutingEngine()
    engine.load_rules()
    return engine


# ── JobStore fixture (in-memory) ──────────────────────────────────────────────
@pytest.fixture
def job_store(tmp_path):
    from livekit.scheduling.store import JobStore
    db_path = str(tmp_path / "test_jobs.db")
    store = JobStore(db_path)
    store.open()
    yield store
    store.close()


# ── ScheduledCallJob fixture ──────────────────────────────────────────────────
@pytest.fixture
def scheduled_job():
    import time
    from livekit.scheduling.models import ScheduledCallJob
    return ScheduledCallJob(
        phone_number="+15551234567",
        scheduled_at=time.time() - 1,   # in the past → immediately due
        lang="en",
        llm="gemini",
        label="test job",
    )


# ── EventHub fixture ──────────────────────────────────────────────────────────
@pytest.fixture
def fresh_hub():
    from livekit.websocket.hub import EventHub
    return EventHub()


# ── AgentInfo fixture ─────────────────────────────────────────────────────────
@pytest.fixture
def english_agent():
    from livekit.routing.engine import AgentInfo
    return AgentInfo(
        agent_id="agent-001",
        name="Alice",
        skills=["english", "billing"],
        available=True,
        max_calls=2,
        active_calls=0,
        node_id="alice-livekit-participant",
    )


@pytest.fixture
def spanish_agent():
    from livekit.routing.engine import AgentInfo
    return AgentInfo(
        agent_id="agent-002",
        name="Carlos",
        skills=["spanish", "billing"],
        available=True,
        max_calls=2,
        active_calls=0,
        node_id="carlos-livekit-participant",
    )
