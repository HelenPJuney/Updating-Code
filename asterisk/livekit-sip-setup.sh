# =============================================================================
# LiveKit SIP Trunk & Dispatch Rule Configuration
# =============================================================================
#
# This file documents how to create the SIP trunk and dispatch rules
# using the LiveKit CLI (lk) or API.
#
# Prerequisites:
#   1. LiveKit server running (docker compose up -d)
#   2. LiveKit SIP server running (livekit-sip container)
#   3. LiveKit CLI installed: https://docs.livekit.io/cli/
#
# Environment:
#   export LIVEKIT_URL=http://localhost:7880
#   export LIVEKIT_API_KEY=devkey
#   export LIVEKIT_API_SECRET=devsecret
#
# =============================================================================

# ─── Step 1: Create Inbound SIP Trunk ────────────────────────────────────────
#
# An inbound trunk tells LiveKit SIP server which SIP sources are allowed
# and how to authenticate them.

# Development (allow all — no auth):
lk sip create-inbound-trunk \
  --name "dev-trunk" \
  --numbers "+1234567890"

# Production (with IP allowlist + auth):
# lk sip create-inbound-trunk \
#   --name "prod-trunk" \
#   --numbers "+1234567890,+0987654321" \
#   --allowed-addresses "203.0.113.10/32,198.51.100.0/24" \
#   --auth-username "asterisk" \
#   --auth-password "your-secure-password"

# ─── Step 2: Create SIP Dispatch Rule ────────────────────────────────────────
#
# A dispatch rule tells LiveKit SIP how to route incoming SIP calls
# to LiveKit rooms.  Two strategies:
#
#   1. Individual rooms (default — each call gets its own room)
#   2. Pin-based (caller enters a PIN to join a specific room)

# Strategy 1: Individual room per call (recommended for AI call center)
# Each incoming SIP call creates a new room named "sip-<unique-id>"
lk sip create-dispatch-rule \
  --trunk-ids "<TRUNK_ID_FROM_STEP_1>" \
  --new-room \
  --room-prefix "sip-"

# Strategy 2: Pin-based (if you need callers to join specific rooms)
# lk sip create-dispatch-rule \
#   --trunk-ids "<TRUNK_ID>" \
#   --request-pin \
#   --room-prefix "sip-"


# ─── Step 3: Configure LiveKit Server Webhooks ──────────────────────────────
#
# LiveKit must send webhooks to your backend so the SIP module
# can trigger AI workers.
#
# Add to your livekit.yaml (or livekit-config configmap):
#
# webhook:
#   urls:
#     - http://your-backend-host:8000/sip/webhook
#   api_key: devkey
#
# In docker-compose, you can use host.docker.internal:
#   - http://host.docker.internal:8000/sip/webhook
#
# Or use the Docker network hostname if your backend is in the same
# docker-compose stack:
#   - http://backend:8000/sip/webhook


# ─── Step 4: Verify Configuration ───────────────────────────────────────────

# List trunks:
lk sip list-trunk

# List dispatch rules:
lk sip list-dispatch-rule

# List active SIP participants:
lk sip list-participant


# ─── Alternative: JSON Configuration (for API / automation) ──────────────────
#
# If you prefer to use the LiveKit API directly instead of the CLI,
# here are the equivalent JSON payloads.

# Create Inbound Trunk (POST /twirp/livekit.SIP/CreateSIPInboundTrunk):
# {
#   "trunk": {
#     "name": "prod-trunk",
#     "numbers": ["+1234567890"],
#     "allowed_addresses": ["203.0.113.10/32"],
#     "auth_username": "asterisk",
#     "auth_password": "your-secure-password"
#   }
# }

# Create Dispatch Rule (POST /twirp/livekit.SIP/CreateSIPDispatchRule):
# {
#   "rule": {
#     "trunk_ids": ["<TRUNK_ID>"],
#     "rule": {
#       "dispatch_rule_individual": {
#         "room_prefix": "sip-"
#       }
#     }
#   }
# }


# ─── Codec Negotiation & Media Flow ─────────────────────────────────────────
#
# PSTN (8kHz PCM μ-law)
#   ↓  SIP INVITE with codec list
# Asterisk (transcodes if needed)
#   ↓  Offers: Opus > G.722 > G.711
# LiveKit SIP Server
#   ↓  Negotiates best codec (prefers Opus)
# LiveKit Room (48kHz Opus)
#   ↓  WebRTC audio track
# AI Worker (subscribes to audio track)
#   ↓  PCM 16kHz for STT (Whisper)
#   ↓  LLM processes text
#   ↓  TTS generates audio (Piper → 22kHz PCM → Opus encode)
# LiveKit Room
#   ↓  WebRTC audio track back
# LiveKit SIP Server
#   ↓  Transcodes Opus → G.711 if needed
# Asterisk → PSTN
#
# Key points:
#   • LiveKit SIP handles Opus ↔ G.711 transcoding automatically
#   • Asterisk should offer Opus as first choice (pjsip.conf: allow=opus)
#   • If Opus is not available, G.722 (16kHz) is preferred over G.711 (8kHz)
#   • The AI worker receives audio from LiveKit SDK — sample rate is handled
#     by the LiveKit client library, NOT by your code
#   • STT (Whisper) expects 16kHz mono PCM — the audio_source module
#     already handles resampling from LiveKit's 48kHz to 16kHz
