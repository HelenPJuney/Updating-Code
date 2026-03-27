

from .sip_config import ENABLE_SIP

if ENABLE_SIP:
    from .sip_ingress import sip_router
    from .sip_session_manager import sip_session_mgr, SipSessionManager
    from .sip_event_handler import SipEventHandler

    __all__ = ["sip_router", "sip_session_mgr", "SipSessionManager", "SipEventHandler", "ENABLE_SIP"]
else:
    __all__ = ["ENABLE_SIP"]
