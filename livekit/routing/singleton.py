"""
routing/singleton.py
────────────────────────────────────────────────────────────────────────────
Module-level RoutingEngine singleton.

Import this everywhere instead of creating local RoutingEngine() instances.
Rules are loaded once at startup and can be hot-reloaded via reload_rules().
"""

import logging
from .engine import RoutingEngine

logger = logging.getLogger("callcenter.routing.singleton")

# ── Single shared instance ────────────────────────────────────────────────────
_routing_engine: RoutingEngine = RoutingEngine()

def get_routing_engine() -> RoutingEngine:
    """Return the process-wide RoutingEngine singleton."""
    return _routing_engine


def load_routing_rules() -> None:
    """Load rules from disk into the singleton. Call once at app startup."""
    try:
        _routing_engine.load_rules()
        logger.info("[RoutingSingleton] rules loaded (%d rules)", len(_routing_engine._loader.rules))
    except Exception as exc:
        logger.error("[RoutingSingleton] failed to load routing rules: %s", exc)


def reload_routing_rules() -> int:
    """Hot-reload rules from disk. Returns the number of rules loaded."""
    count = _routing_engine.reload_rules()
    logger.info("[RoutingSingleton] rules reloaded (%d rules)", count)
    return count
