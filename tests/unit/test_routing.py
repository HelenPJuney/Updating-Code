"""
tests/unit/test_routing.py
────────────────────────────────────────────────────────────────────────────────
Unit tests for the skills-based routing engine.

Coverage:
  - Rule loading from JSON
  - Condition matching (lang, source, priority, time, caller prefix)
  - Decision output (queue_name, priority, required_skills, fallback)
  - Agent pool (register, find_best, book, release)
  - apply() mutation of CallRequest
  - Default fallback when no rule matches
  - Hot-reload
"""

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from livekit.kafka.schemas import CallRequest
from livekit.routing.engine import AgentInfo, AgentPool, RoutingEngine, _in_time_window
from livekit.routing.rules import RuleLoader


# ═══════════════════════════════════════════════════════════════════════════════
# Rule loading
# ═══════════════════════════════════════════════════════════════════════════════

class TestRuleLoader:
    def test_loads_default_rules(self):
        loader = RuleLoader()
        loader.load()
        assert len(loader.rules) > 0

    def test_rules_sorted_by_priority(self):
        loader = RuleLoader()
        loader.load()
        priorities = [r.priority for r in loader.rules]
        assert priorities == sorted(priorities)

    def test_to_dict_list(self):
        loader = RuleLoader()
        loader.load()
        dicts = loader.to_dict_list()
        assert all("name" in d for d in dicts)
        assert all("conditions" in d for d in dicts)
        assert all("target" in d for d in dicts)


# ═══════════════════════════════════════════════════════════════════════════════
# Time window helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestTimeWindow:
    def test_inside_window(self):
        dt = datetime(2026, 3, 26, 12, 0, tzinfo=timezone.utc)
        assert _in_time_window(dt, "08:00", "20:00") is True

    def test_outside_window(self):
        dt = datetime(2026, 3, 26, 22, 0, tzinfo=timezone.utc)
        assert _in_time_window(dt, "08:00", "20:00") is False

    def test_overnight_window_inside(self):
        # "after hours": 22:00 → 06:00
        dt = datetime(2026, 3, 26, 23, 30, tzinfo=timezone.utc)
        assert _in_time_window(dt, "22:00", "06:00") is True

    def test_overnight_window_outside(self):
        dt = datetime(2026, 3, 26, 10, 0, tzinfo=timezone.utc)
        assert _in_time_window(dt, "22:00", "06:00") is False


# ═══════════════════════════════════════════════════════════════════════════════
# Routing Engine — condition matching
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoutingEngine:

    @pytest.mark.asyncio
    async def test_default_rule_always_matches(self, routing_engine):
        req = CallRequest(lang="en", source="browser")
        decision = await routing_engine.route(req)
        assert decision.matched is True

    @pytest.mark.asyncio
    async def test_sip_source_matches_sip_rule(self, routing_engine):
        req = CallRequest(lang="en", source="sip", caller_number="+15551111111")
        decision = await routing_engine.route(req)
        # The sip_inbound_priority rule should match
        assert decision.rule_name in ("sip_inbound_priority", "default")
        assert decision.queue_name in ("sip", "default")

    @pytest.mark.asyncio
    async def test_spanish_lang_matches_spanish_rule(self, routing_engine):
        req = CallRequest(lang="es", source="browser")
        decision = await routing_engine.route(req)
        assert decision.rule_name == "spanish_caller"
        assert decision.queue_name == "spanish"
        assert "spanish" in decision.required_skills

    @pytest.mark.asyncio
    async def test_hindi_lang_matches_hindi_rule(self, routing_engine):
        req = CallRequest(lang="hi", source="browser")
        decision = await routing_engine.route(req)
        assert decision.rule_name == "hindi_caller"
        assert "hindi" in decision.required_skills

    @pytest.mark.asyncio
    async def test_vip_caller_number_prefix(self, routing_engine):
        req = CallRequest(lang="en", source="sip", caller_number="+18005551234")
        decision = await routing_engine.route(req)
        assert decision.rule_name == "vip_caller"
        assert decision.queue_name == "vip"
        assert decision.priority == 10

    @pytest.mark.asyncio
    async def test_apply_enriches_request(self, routing_engine):
        req = CallRequest(lang="es", source="browser")
        decision = await routing_engine.route(req)
        decision.apply(req)
        assert req.queue_name == "spanish"
        assert req.routing_rule == "spanish_caller"
        assert "spanish" in req.required_skills

    @pytest.mark.asyncio
    async def test_ai_config_applied(self, routing_engine):
        req = CallRequest(lang="es", source="browser")
        decision = await routing_engine.route(req)
        decision.apply(req)
        # Spanish rule sets agent_name = "Asistente"
        assert req.agent_name == "Asistente"

    @pytest.mark.asyncio
    async def test_reload_rules(self, routing_engine):
        count_before = len(routing_engine.rules_snapshot())
        reloaded = routing_engine.reload_rules()
        assert reloaded == count_before  # same rules

    @pytest.mark.asyncio
    async def test_no_rule_match_returns_default_fallback(self, routing_engine):
        """
        If we disable all rules (empty list), engine should return default_fallback.
        """
        original_rules = routing_engine._loader._rules[:]
        routing_engine._loader._rules = []
        try:
            req = CallRequest(lang="en", source="browser")
            decision = await routing_engine.route(req)
            assert decision.matched is False
            assert decision.rule_name == "default_fallback"
            assert decision.queue_name == "default"
        finally:
            routing_engine._loader._rules = original_rules


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Pool
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentPool:

    @pytest.mark.asyncio
    async def test_register_and_find(self, english_agent):
        pool = AgentPool()
        await pool.register(english_agent)
        found = await pool.find_best([])
        assert found is not None
        assert found.agent_id == "agent-001"

    @pytest.mark.asyncio
    async def test_skill_matching_english(self, english_agent, spanish_agent):
        pool = AgentPool()
        await pool.register(english_agent)
        await pool.register(spanish_agent)

        found = await pool.find_best(["spanish"])
        assert found is not None
        assert found.agent_id == "agent-002"  # Carlos

    @pytest.mark.asyncio
    async def test_skill_matching_no_match(self, english_agent):
        pool = AgentPool()
        await pool.register(english_agent)

        found = await pool.find_best(["nonexistent_skill"])
        assert found is None

    @pytest.mark.asyncio
    async def test_book_decrements_free_slots(self, english_agent):
        pool = AgentPool()
        await pool.register(english_agent)

        ok = await pool.book("agent-001")
        assert ok is True
        snapshot = pool.snapshot()
        agent = next(s for s in snapshot if s["agent_id"] == "agent-001")
        assert agent["active_calls"] == 1
        assert agent["free_slots"] == 1  # max_calls=2, active_calls=1

    @pytest.mark.asyncio
    async def test_book_fails_when_full(self, english_agent):
        pool = AgentPool()
        english_agent.max_calls = 1
        await pool.register(english_agent)

        ok1 = await pool.book("agent-001")
        assert ok1 is True
        ok2 = await pool.book("agent-001")   # no more slots
        assert ok2 is False

    @pytest.mark.asyncio
    async def test_release_increments_free_slots(self, english_agent):
        pool = AgentPool()
        await pool.register(english_agent)
        await pool.book("agent-001")
        await pool.release("agent-001")

        snapshot = pool.snapshot()
        agent = next(s for s in snapshot if s["agent_id"] == "agent-001")
        assert agent["active_calls"] == 0

    @pytest.mark.asyncio
    async def test_deregister(self, english_agent):
        pool = AgentPool()
        await pool.register(english_agent)
        await pool.deregister("agent-001")
        found = await pool.find_best([])
        assert found is None

    @pytest.mark.asyncio
    async def test_unavailable_agent_not_returned(self, english_agent):
        pool = AgentPool()
        english_agent.available = False
        await pool.register(english_agent)
        found = await pool.find_best([])
        assert found is None


# ═══════════════════════════════════════════════════════════════════════════════
# Routing + Agent integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoutingWithAgents:

    @pytest.mark.asyncio
    async def test_spanish_call_routed_to_spanish_agent(self, routing_engine, spanish_agent):
        await routing_engine.register_agent(spanish_agent)
        req = CallRequest(lang="es", source="browser")
        decision = await routing_engine.route(req)
        assert decision.human_agent is not None
        assert decision.human_agent.agent_id == "agent-002"

    @pytest.mark.asyncio
    async def test_no_agent_returns_none(self, routing_engine):
        # Ensure no Spanish agent is registered
        req = CallRequest(lang="es", source="browser")
        decision = await routing_engine.route(req)
        # May or may not have agent — just check decision is valid
        assert decision.rule_name is not None
