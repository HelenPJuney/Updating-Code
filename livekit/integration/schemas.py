from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

class CallStartRequest(BaseModel):
    """Start an outbound SIP/PSTN call via the integration API."""
    phone_number: str
    lang: str = "en"
    source: str = "external_sip"       # "external_sip" | "external_browser"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class BrowserCallStartRequest(BaseModel):
    """Start a browser WebRTC call via the integration API."""
    caller_id: str = ""               # user email, id, or opaque identifier
    lang: str = "en"
    priority: int = 0
    source: str = "external_browser"   # always external_browser for this endpoint
    metadata: Dict[str, Any] = Field(default_factory=dict)

class CallStartResponse(BaseModel):
    session_id: str
    room_id: str = ""
    token: str = ""                    # LiveKit token (non-empty for browser calls)
    livekit_url: str = ""
    status: str

class CallStatusResponse(BaseModel):
    session_id: str
    status: str
    assigned_agent: Optional[str] = None
    created_at: float
    updated_at: float

class WebhookRegisterRequest(BaseModel):
    url: str
    events: List[str] = Field(default_factory=lambda: ["call_started", "call_completed", "call_failed"])
    secret: str = ""                   # HMAC-SHA256 signing secret

class WebhookRegisterResponse(BaseModel):
    status: str
    message: str
