# [ START ]
#     |
#     v
# +--------------------------+
# | list_rules()             |
# | * GET /routing/rules     |
# +--------------------------+
#     |
#     |----> routing_engine.rules_snapshot()
#     v
# +--------------------------+
# | reload_rules()           |
# | * POST /rules/reload     |
# +--------------------------+
#     |
#     |----> routing_engine.reload_rules()
#     v
# +--------------------------+
# | list_agents()            |
# | * GET /routing/agents    |
# +--------------------------+
#     |
#     |----> routing_engine.agents_snapshot()
#     v
# +--------------------------+
# | register_agent(req)      |
# | * POST /routing/agents   |
# +--------------------------+
#     |
#     |----> Map req -> AgentInfo object
#     |----> routing_engine.register_agent()
#     v
# +--------------------------+
# | deregister_agent(id)     |
# | * DELETE /agents/{id}    |
# +--------------------------+
#     |
#     |----> routing_engine.deregister_agent()
#     v
# +--------------------------+
# | agent_heartbeat(id)      |
# | * POST /agents/{id}/hb   |
# +--------------------------+
#     |
#     |----> routing_engine.agent_heartbeat()
#     v
# +--------------------------+
# | release_agent_slot(id)   |
# | * POST /agents/{id}/rel  |
# +--------------------------+
#     |
#     |----> routing_engine.release_agent()
#     v
# +--------------------------+
# | test_routing_decision()  |
# | * POST /decision         |
# +--------------------------+
#     |
#     |----> Create dummy CallRequest
#     |----> routing_engine.route(dummy)
#     |----> Return matched rules & AI/Human config
#     v
# [ YIELD ]

import logging
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("callcenter.routing.api")

routing_router = APIRouter(prefix="/routing", tags=["routing"])


# ── Pydantic request/response models ─────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    agent_id:     str
    name:         str              = ""
    skills:       List[str]        = []
    available:    bool             = True
    max_calls:    int              = 1
    node_id:      str              = ""   # LiveKit participant identity


class RoutingTestRequest(BaseModel):
    lang:          str   = "en"
    source:        str   = "browser"
    priority:      int   = 0
    caller_number: str   = ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@routing_router.get("/rules")
async def list_rules():
    """List all routing rules in evaluation order."""
    from . import routing_engine
    return {
        "rules": routing_engine.rules_snapshot(),
        "count": len(routing_engine.rules_snapshot()),
        "timestamp": time.time(),
    }


@routing_router.post("/rules/reload")
async def reload_rules():
    """Hot-reload routing rules from disk without restarting."""
    from . import routing_engine
    count = routing_engine.reload_rules()
    logger.info("[RoutingAPI] rules reloaded: %d rules", count)
    return {"status": "ok", "rules_loaded": count, "timestamp": time.time()}


@routing_router.get("/agents")
async def list_agents():
    """List all registered human agents and their availability."""
    from . import routing_engine
    return {
        "agents": routing_engine.agents_snapshot(),
        "timestamp": time.time(),
    }


@routing_router.post("/agents")
async def register_agent(req: AgentRegisterRequest):
    """Register or update a human agent in the routing pool."""
    from . import routing_engine
    from .engine import AgentInfo

    info = AgentInfo(
        agent_id=req.agent_id,
        name=req.name,
        skills=req.skills,
        available=req.available,
        max_calls=req.max_calls,
        active_calls=0,
        node_id=req.node_id,
    )
    await routing_engine.register_agent(info)
    return {
        "status": "registered",
        "agent_id": req.agent_id,
        "skills": req.skills,
        "timestamp": time.time(),
    }


@routing_router.delete("/agents/{agent_id}")
async def deregister_agent(agent_id: str):
    """Remove a human agent from the routing pool."""
    from . import routing_engine
    await routing_engine.deregister_agent(agent_id)
    return {"status": "deregistered", "agent_id": agent_id}


@routing_router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str):
    """Keep an agent alive (call every 30s)."""
    from . import routing_engine
    await routing_engine.agent_heartbeat(agent_id)
    return {"status": "ok", "agent_id": agent_id, "timestamp": time.time()}


@routing_router.post("/agents/{agent_id}/release")
async def release_agent_slot(agent_id: str):
    """Free one call slot for an agent after a call ends."""
    from . import routing_engine
    await routing_engine.release_agent(agent_id)
    return {"status": "released", "agent_id": agent_id}


@routing_router.post("/decision")
async def test_routing_decision(req: RoutingTestRequest):
    """
    Dry-run routing decision for given call parameters.
    Does NOT book any agent — use for testing rule logic.
    """
    from . import routing_engine
    from ..kafka.schemas import CallRequest

    dummy = CallRequest(
        lang=req.lang,
        source=req.source,
        priority=req.priority,
        caller_number=req.caller_number,
    )
    decision = await routing_engine.route(dummy)
    return {
        "matched":         decision.matched,
        "rule_name":       decision.rule_name,
        "queue_name":      decision.queue_name,
        "priority":        decision.priority,
        "required_skills": decision.required_skills,
        "fallback_action": decision.fallback_action,
        "ai_config":       decision.ai_config,
        "human_agent_id":  decision.human_agent.agent_id if decision.human_agent else None,
        "timestamp":       decision.ts,
    }
