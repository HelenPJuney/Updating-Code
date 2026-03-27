# [ START ]
#     |
#     v
# +--------------------------+
# | route(req)               |
# | * Core logic entry point |
# | * Evaluates all rules    |
# +--------------------------+
#     |
#     | [ 1. Rule Matching ]
#     |----> _matches(cond, req, now) 
#     |        |-- Checks: Lang, Source, Priority
#     |        |-- Checks: Caller Prefix
#     |        |-- Checks: UTC Time Window (_in_time_window)
#     |
#     | [ 2. Rule Not Found ]
#     |----> Return "default_fallback" Decision
#     |
#     | [ 3. Rule Matched ]
#     |----> Extract target (queue, skills, ai_config)
#     |
#     | [ 4. Human Agent Lookup ]
#     |----> AgentPool.find_best(required_skills)
#     |        |-- Filter: Available + Free Slots + Skills
#     |        |-- Pick: Agent with most free slots
#     |
#     | [ 5. Booking ]
#     |----> AgentPool.book(agent_id)
#     |        |-- Increment active_calls
#     |
#     v
# +--------------------------+
# | RoutingDecision          |
# | * Immutable Result       |
# +--------------------------+
#     |
#     |----> apply(req)
#     |        |-- Mutates req with queue/priority
#     |        |-- Injects AI config (LLM/Voice)
#     |        |-- Sets escalation_target (node_id)
#     v
# +--------------------------+
# | Agent & Rule Management  |
# | * register_agent()       |
# | * deregister_agent()     |
# | * release_agent()        |
# | * reload_rules()         |
# +--------------------------+
#     |
#     v
# [ YIELD ]


import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .rules import RuleConditions, RuleLoader, RoutingRule, RuleTarget

logger = logging.getLogger("callcenter.routing.engine")


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Pool — human agent availability registry
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AgentInfo:
    agent_id:     str
    name:         str
    skills:       List[str]
    available:    bool
    max_calls:    int
    active_calls: int
    node_id:      str          # LiveKit participant identity
    registered_at: float = field(default_factory=time.time)
    last_seen:    float   = field(default_factory=time.time)

    @property
    def free_slots(self) -> int:
        return max(0, self.max_calls - self.active_calls)

    def has_skills(self, required: List[str]) -> bool:
        if not required:
            return True
        return all(s in self.skills for s in required)


class AgentPool:
    """Thread-safe in-memory human agent registry."""

    def __init__(self) -> None:
        self._agents: Dict[str, AgentInfo] = {}
        self._lock = asyncio.Lock()

    async def register(self, info: AgentInfo) -> None:
        async with self._lock:
            self._agents[info.agent_id] = info
            logger.info(
                "[AgentPool] registered agent=%s skills=%s",
                info.agent_id, info.skills,
            )

    async def deregister(self, agent_id: str) -> None:
        async with self._lock:
            self._agents.pop(agent_id, None)
            logger.info("[AgentPool] deregistered agent=%s", agent_id)

    async def find_best(self, required_skills: List[str]) -> Optional[AgentInfo]:
        """
        Find the best available human agent matching required_skills.
        Picks agent with most free slots among qualified candidates.
        Returns None if no agent available.
        """
        async with self._lock:
            candidates = [
                a for a in self._agents.values()
                if a.available and a.free_slots > 0 and a.has_skills(required_skills)
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda a: a.free_slots)

    async def book(self, agent_id: str) -> bool:
        """Reserve one call slot for the agent. Returns False if no slot."""
        async with self._lock:
            agent = self._agents.get(agent_id)
            if not agent or agent.free_slots <= 0:
                return False
            agent.active_calls += 1
            return True

    async def release(self, agent_id: str) -> None:
        """Free one call slot after the call ends."""
        async with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.active_calls = max(0, agent.active_calls - 1)

    async def heartbeat(self, agent_id: str) -> None:
        async with self._lock:
            if agent_id in self._agents:
                self._agents[agent_id].last_seen = time.time()

    def snapshot(self) -> List[Dict]:
        return [
            {
                "agent_id":     a.agent_id,
                "name":         a.name,
                "skills":       a.skills,
                "available":    a.available,
                "max_calls":    a.max_calls,
                "active_calls": a.active_calls,
                "free_slots":   a.free_slots,
                "node_id":      a.node_id,
                "last_seen":    a.last_seen,
            }
            for a in self._agents.values()
        ]


# ═══════════════════════════════════════════════════════════════════════════════
# Routing Decision
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RoutingDecision:
    """
    Immutable result of routing a call.
    Call decision.apply(req) to enrich the CallRequest before Kafka submission.
    """
    rule_name:        str
    queue_name:       str
    priority:         int
    required_skills:  List[str]
    fallback_action:  str           # "queue" | "voicemail" | "callback" | "ai_bot"
    ai_config:        Dict          # LLM/voice/agent_name overrides
    human_agent:      Optional[AgentInfo]   # non-None → route to human
    matched:          bool          # True = a rule fired; False = default fallback
    ts:               float = field(default_factory=time.time)

    def apply(self, req) -> object:
        """
        Enrich a CallRequest with routing metadata.
        Returns the same object (mutated in-place) for chaining.
        """
        req.queue_name       = self.queue_name
        req.priority         = self.priority
        req.required_skills  = self.required_skills
        req.routing_rule     = self.rule_name
        req.fallback_action  = self.fallback_action

        # Apply AI config overrides
        if self.ai_config.get("llm"):
            req.llm = self.ai_config["llm"]
        if self.ai_config.get("voice"):
            req.voice = self.ai_config["voice"]
        if self.ai_config.get("agent_name"):
            req.agent_name = self.ai_config["agent_name"]

        # Escalation target (human agent)
        if self.human_agent:
            req.escalation_target = self.human_agent.node_id
        return req


# ═══════════════════════════════════════════════════════════════════════════════
# Routing Engine
# ═══════════════════════════════════════════════════════════════════════════════

class RoutingEngine:
    """
    Stateless rule evaluator + AgentPool lookup.

    Usage:
        engine = RoutingEngine()
        engine.load_rules()
        decision = await engine.route(call_request)
        call_request = decision.apply(call_request)
    """

    def __init__(self) -> None:
        self._loader = RuleLoader()
        self._pool   = AgentPool()
        self._lock   = asyncio.Lock()

    def load_rules(self) -> None:
        """Load rules from disk (call at startup and on hot-reload)."""
        self._loader.load()

    # ── Agent registration ────────────────────────────────────────────────────

    async def register_agent(self, info: AgentInfo) -> None:
        await self._pool.register(info)

    async def deregister_agent(self, agent_id: str) -> None:
        await self._pool.deregister(agent_id)

    async def release_agent(self, agent_id: str) -> None:
        await self._pool.release(agent_id)

    async def agent_heartbeat(self, agent_id: str) -> None:
        await self._pool.heartbeat(agent_id)

    def agents_snapshot(self) -> List[Dict]:
        return self._pool.snapshot()

    # ── Core routing logic ────────────────────────────────────────────────────

    async def route(self, req) -> RoutingDecision:
        """
        Evaluate routing rules against a CallRequest.

        Returns a RoutingDecision with all metadata needed to enrich the req.
        Does NOT mutate req — caller must call decision.apply(req).
        """
        now_utc = datetime.now(timezone.utc)
        matched_rule: Optional[RoutingRule] = None

        for rule in self._loader.rules:
            if not rule.enabled:
                continue
            if self._matches(rule.conditions, req, now_utc):
                matched_rule = rule
                break  # first-match wins

        if matched_rule is None:
            # No rule matched → use safe defaults
            return RoutingDecision(
                rule_name       = "default_fallback",
                queue_name      = "default",
                priority        = getattr(req, "priority", 0),
                required_skills = [],
                fallback_action = "queue",
                ai_config       = {},
                human_agent     = None,
                matched         = False,
            )

        target    = matched_rule.target
        priority  = (
            target.priority_override
            if target.priority_override is not None
            else getattr(req, "priority", 0)
        )

        # Look for an available human agent with the required skills
        human_agent: Optional[AgentInfo] = None
        if target.required_skills:
            human_agent = await self._pool.find_best(target.required_skills)
            if human_agent:
                await self._pool.book(human_agent.agent_id)
                logger.info(
                    "[Routing] matched human agent=%s skills=%s for session=%s",
                    human_agent.agent_id, human_agent.skills,
                    getattr(req, "session_id", "?")[:8],
                )

        logger.info(
            "[Routing] rule=%s queue=%s priority=%d skills=%s fallback=%s session=%s",
            matched_rule.name, target.queue_name, priority,
            target.required_skills, target.fallback_action,
            getattr(req, "session_id", "?")[:8],
        )

        return RoutingDecision(
            rule_name       = matched_rule.name,
            queue_name      = target.queue_name,
            priority        = priority,
            required_skills = target.required_skills,
            fallback_action = target.fallback_action,
            ai_config       = dict(target.ai_config),
            human_agent     = human_agent,
            matched         = True,
        )

    # ── Condition evaluator ───────────────────────────────────────────────────

    @staticmethod
    def _matches(cond: RuleConditions, req, now_utc: datetime) -> bool:
        """Return True only if ALL defined conditions are satisfied."""

        # lang match
        if cond.lang is not None:
            if getattr(req, "lang", "en") not in cond.lang:
                return False

        # source match
        if cond.source is not None:
            if getattr(req, "source", "browser") not in cond.source:
                return False

        # priority threshold
        if cond.priority_gte:
            if getattr(req, "priority", 0) < cond.priority_gte:
                return False

        # caller number prefix
        if cond.caller_number_prefix:
            caller = getattr(req, "caller_number", "")
            if not any(caller.startswith(pfx) for pfx in cond.caller_number_prefix):
                return False

        # time of day: INSIDE window
        if cond.time_of_day_utc_between:
            start_s, end_s = cond.time_of_day_utc_between
            if not _in_time_window(now_utc, start_s, end_s):
                return False

        # time of day: OUTSIDE window (after-hours)
        if cond.time_of_day_utc_outside:
            start_s, end_s = cond.time_of_day_utc_outside
            if _in_time_window(now_utc, start_s, end_s):
                return False

        return True

    # ── Rule management helpers ───────────────────────────────────────────────

    def rules_snapshot(self) -> List[Dict]:
        return self._loader.to_dict_list()

    def reload_rules(self) -> int:
        self._loader.load()
        return len(self._loader.rules)


# ── Time window helper ────────────────────────────────────────────────────────

def _in_time_window(now: datetime, start_hhmm: str, end_hhmm: str) -> bool:
    """
    Returns True if `now` (UTC) falls within [start_hhmm, end_hhmm).
    Both are "HH:MM" strings in 24-hour format.
    Handles overnight windows (e.g. "22:00" → "06:00").
    """
    try:
        sh, sm = map(int, start_hhmm.split(":"))
        eh, em = map(int, end_hhmm.split(":"))
        start_min = sh * 60 + sm
        end_min   = eh * 60 + em
        cur_min   = now.hour * 60 + now.minute

        if start_min <= end_min:
            return start_min <= cur_min < end_min
        else:
            # Overnight window: e.g. 22:00 → 06:00
            return cur_min >= start_min or cur_min < end_min
    except Exception:
        return True  # safe default: always match on parse error
