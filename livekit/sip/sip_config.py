"""
SIP configuration — environment variables and feature flags.

All SIP-related env vars are centralised here.  The ENABLE_SIP flag gates
the entire SIP module; when False, no SIP routes are mounted and no webhook
listener is started.

Environment Variables:
    Feature:
        ENABLE_SIP              — "true" to activate (default: "false")

    LiveKit SIP:
        SIP_TRUNK_ID            — LiveKit SIP trunk identifier
        SIP_DISPATCH_RULE_ID    — LiveKit SIP dispatch rule ID

    Security:
        SIP_WEBHOOK_SECRET      — HMAC secret for validating LiveKit webhooks
        SIP_ENFORCE_SIGNATURE   — Reject unsigned webhooks (default: "false" for dev)
        SIP_RATE_LIMIT_MAX      — Max SIP webhook hits per window (default: 60)
        SIP_RATE_LIMIT_WINDOW   — Rate limit window in seconds (default: 60)
        SIP_ALLOWED_CALLERS     — Comma-separated allowed caller IDs (empty = all)

    Defaults for SIP Calls:
        SIP_DEFAULT_LANG        — Default language (default: "en")
        SIP_DEFAULT_LLM         — Default LLM (default: "gemini")
        SIP_DEFAULT_VOICE       — Default TTS voice stem
        SIP_DEFAULT_AGENT_NAME  — AI agent display name (default: "Assistant")

    Call Behaviour:
        SIP_AUTO_ANSWER         — Auto-answer incoming SIP calls (default: "true")
        SIP_CALL_TIMEOUT_SEC    — Max call duration before force-end (default: 3600)
        SIP_RINGING_TIMEOUT_SEC — Time before ringing call is considered dead (default: 30)

    Retry:
        SIP_RETRY_MAX           — Max retries for SIP-to-Kafka bridge (default: 3)
        SIP_RETRY_DELAY_SEC     — Base retry delay in seconds (default: 1.0)

    Asterisk / Media:
        ASTERISK_SIP_HOST       — Asterisk SIP server address (default: "localhost")
        ASTERISK_SIP_PORT       — Asterisk SIP port (default: 5060)
        SIP_RTP_PORT_START      — RTP port range start (default: 10000)
        SIP_RTP_PORT_END        — RTP port range end (default: 20000)
"""

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
