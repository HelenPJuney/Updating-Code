
# ── Namespace package merge ────────────────────────────────────────────────────
# The installed livekit SDK (livekit-api, livekit-rtc) is a namespace package
# with no __init__.py. This local livekit/ package (which HAS an __init__.py)
# would normally shadow it. We fix this by appending the installed SDK path to
# __path__ so that `from livekit.api import ...` still resolves correctly.
import sys as _sys
from pathlib import Path as _Path

def _extend_livekit_path():
    _here = _Path(__file__).parent
    for _p in _sys.path:
        _candidate = _Path(_p) / "livekit"
        if _candidate != _here and (_candidate / "api").exists():
            if str(_candidate) not in __path__:
                __path__.append(str(_candidate))
            break

_extend_livekit_path()
# ──────────────────────────────────────────────────────────────────────────────

from .ai_worker import livekit_router
from .kafka.health import kafka_health_router
from .browser import browser_router

# ── FIX #3: Load routing rules into the process-wide singleton at import time ──
# All routers (browser, SIP, integration) call get_routing_engine() to get this
from .routing.singleton import load_routing_rules, get_routing_engine
try:
    load_routing_rules()
except Exception:
    pass  # non-fatal: rules will be empty, default_fallback rule will apply

# ── SIP/PSTN support (conditionally loaded via ENABLE_SIP env var) ────────────
sip_router = None
try:
    from .sip import ENABLE_SIP
    if ENABLE_SIP:
        from .sip import sip_router as _sip_router
        sip_router = _sip_router
except ImportError:
    pass

__all__ = [
    "livekit_router",
    "kafka_health_router",
    "sip_router",
    "browser_router",
    "load_routing_rules",
    "get_routing_engine",
]

def _attach_event_hooks() -> None:
  
    try:
        from .websocket import event_hub
        from .kafka import worker_service as _ws_mod

        _orig_started   = _ws_mod.WorkerService._publish_started.__func__   if hasattr(_ws_mod.WorkerService._publish_started, "__func__") else None
        _orig_completed = _ws_mod.WorkerService._publish_completed.__func__ if hasattr(_ws_mod.WorkerService._publish_completed, "__func__") else None

        pass
    except Exception:
        pass

_attach_event_hooks()
