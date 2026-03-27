

import os

# ── Feature flag ──────────────────────────────────────────────────────────────
ENABLE_SIP: bool = os.getenv("ENABLE_SIP", "false").lower() in ("true", "1", "yes")

# ── LiveKit SIP server ────────────────────────────────────────────────────────
SIP_TRUNK_ID:         str = os.getenv("SIP_TRUNK_ID", "")
SIP_DISPATCH_RULE_ID: str = os.getenv("SIP_DISPATCH_RULE_ID", "")

# ── Webhook security ─────────────────────────────────────────────────────────
SIP_WEBHOOK_SECRET: str = os.getenv(
    "SIP_WEBHOOK_SECRET",
    os.getenv("LIVEKIT_API_SECRET", "devsecret"),
)
# In production, set this to true to reject unsigned webhooks (401)
SIP_ENFORCE_SIGNATURE: bool = os.getenv(
    "SIP_ENFORCE_SIGNATURE", "false"
).lower() in ("true", "1", "yes")

# ── Rate limiting ─────────────────────────────────────────────────────────────
SIP_RATE_LIMIT_MAX:    int   = int(os.getenv("SIP_RATE_LIMIT_MAX", "60"))
SIP_RATE_LIMIT_WINDOW: float = float(os.getenv("SIP_RATE_LIMIT_WINDOW", "60"))

# ── Caller allowlist ──────────────────────────────────────────────────────────
# Comma-separated list of allowed caller numbers/URIs.  Empty = all allowed.
_raw_allowed = os.getenv("SIP_ALLOWED_CALLERS", "")
SIP_ALLOWED_CALLERS: list[str] = [
    c.strip() for c in _raw_allowed.split(",") if c.strip()
]

# ── Default call parameters for SIP callers ───────────────────────────────────
SIP_DEFAULT_LANG:       str = os.getenv("SIP_DEFAULT_LANG",       "en")
SIP_DEFAULT_LLM:        str = os.getenv("SIP_DEFAULT_LLM",        "gemini")
SIP_DEFAULT_VOICE:      str = os.getenv("SIP_DEFAULT_VOICE",      "")
SIP_DEFAULT_AGENT_NAME: str = os.getenv("SIP_DEFAULT_AGENT_NAME", "Assistant")

# ── Call behaviour ────────────────────────────────────────────────────────────
SIP_AUTO_ANSWER:        bool  = os.getenv("SIP_AUTO_ANSWER", "true").lower() in ("true", "1", "yes")
SIP_CALL_TIMEOUT_SEC:   int   = int(os.getenv("SIP_CALL_TIMEOUT_SEC", "3600"))    # 1 hour max
SIP_RINGING_TIMEOUT_SEC: int  = int(os.getenv("SIP_RINGING_TIMEOUT_SEC", "30"))   # 30s to answer

# ── Retry settings for SIP→Kafka bridge ───────────────────────────────────────
SIP_RETRY_MAX:       int   = int(os.getenv("SIP_RETRY_MAX", "3"))
SIP_RETRY_DELAY_SEC: float = float(os.getenv("SIP_RETRY_DELAY_SEC", "1.0"))

# ── SIP participant identity prefix ──────────────────────────────────────────
SIP_PARTICIPANT_PREFIX: str = "sip_"

# ── Asterisk / Media ─────────────────────────────────────────────────────────
ASTERISK_SIP_HOST:  str = os.getenv("ASTERISK_SIP_HOST", "localhost")
ASTERISK_SIP_PORT:  int = int(os.getenv("ASTERISK_SIP_PORT", "5060"))
SIP_RTP_PORT_START: int = int(os.getenv("SIP_RTP_PORT_START", "10000"))
SIP_RTP_PORT_END:   int = int(os.getenv("SIP_RTP_PORT_END",   "20000"))
