# [ START: APP INITIALIZATION / RELOAD ]
#       |
#       v
# +------------------------------------------+
# | singleton.py Initialization              |
# | * Create private _routing_engine         |
# +------------------------------------------+
#       |
#       |--- load_routing_rules() (App Startup)
#       |    * Trigger engine.load_rules()
#       |    * Log initial rule count
#       |
#       |--- reload_routing_rules() (Hot-Reload)
#       |    * Trigger engine.reload_rules()
#       |    * Return updated rule count
#       v
# +------------------------------------------+
# | get_routing_engine()                     |
# | * Return the shared _routing_engine      |
# +------------------------------------------+
#       |
#       |---- Used by:
#       |      * browser/router.py
#       |      * integration/service.py
#       |      * routing/api.py
#       v
# [ UNIFIED ROUTING LOGIC APPLIED ]

import logging
from .engine import RoutingEngine

logger = logging.getLogger("callcenter.routing.singleton")

# ── Single shared instance ────────────────────────────────────────────────────
_routing_engine: RoutingEngine = RoutingEngine()

def get_routing_engine() -> RoutingEngine:
    """Return the process-wide RoutingEngine singleton."""
    logger.debug("Executing get_routing_engine")
    return _routing_engine


def load_routing_rules() -> None:
    """Load rules from disk into the singleton. Call once at app startup."""
    logger.debug("Executing load_routing_rules")
    try:
        _routing_engine.load_rules()
        logger.info("[RoutingSingleton] rules loaded (%d rules)", len(_routing_engine._loader.rules))
    except Exception as exc:
        logger.error("[RoutingSingleton] failed to load routing rules: %s", exc)


def reload_routing_rules() -> int:
    """Hot-reload rules from disk. Returns the number of rules loaded."""
    logger.debug("Executing reload_routing_rules")
    count = _routing_engine.reload_rules()
    logger.info("[RoutingSingleton] rules reloaded (%d rules)", count)
    return count
