"""
Proxy package: makes 'backend.core', 'backend.services', 'backend.webrtc'
resolve to the real Voice Ai Core Backend project.
"""
import sys
import os

_REAL_BACKEND = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..",  # up to Desktop
    "stable-code",
    "Voice Ai Core Backend",
)
_REAL_BACKEND = os.path.normpath(_REAL_BACKEND)

# Insert the real backend's parent (stable-code) so Python sees it as 'backend'
_STABLE_CODE = os.path.dirname(_REAL_BACKEND)
if _STABLE_CODE not in sys.path:
    sys.path.insert(0, _STABLE_CODE)

# The real package lives in a folder with spaces — create a 'backend' alias
import importlib, types

def _load_real():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "backend",
        os.path.join(_REAL_BACKEND, "__init__.py"),
        submodule_search_locations=[_REAL_BACKEND],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [_REAL_BACKEND]
    mod.__package__ = "backend"
    sys.modules["backend"] = mod
    spec.loader.exec_module(mod)
    return mod

if "backend" not in sys.modules or sys.modules["backend"].__file__ != os.path.join(_REAL_BACKEND, "__init__.py"):
    _load_real()
