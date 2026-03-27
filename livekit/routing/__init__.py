

from .engine import RoutingEngine, RoutingDecision
from .rules import RuleLoader
from .api import routing_router

# Module-level singleton
routing_engine = RoutingEngine()

__all__ = ["routing_engine", "routing_router", "RoutingEngine", "RoutingDecision", "RuleLoader"]
