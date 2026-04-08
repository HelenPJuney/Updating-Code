"""
config.py — Central configuration for the IVR routing system.
All values are loaded from environment variables with sane defaults
optimized for potato-PC operation.
"""

import logging
logger = logging.getLogger(__name__)

import os
from pathlib import Path

# ── Piper TTS (pre-synthesized greetings + live synthesis) ────────────────────
PIPER_EXE   = os.getenv("PIPER_EXECUTABLE", "piper/piper.exe")
PIPER_MODEL = os.getenv("PIPER_MODEL", "piper/models/en_US-ryan-high.onnx")
ESPEAK_DATA = str(Path("piper/espeak-ng-data").absolute())

# ── Faster-Whisper STT ────────────────────────────────────────────────────────
# tiny.en = ~39MB, int8 = minimal RAM. Perfect for potato.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "tiny.en")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")

# ── Gemini API (LLM stays off the potato — cloud only) ───────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── IVR Timing (generous for potato) ─────────────────────────────────────────
SILENCE_THRESHOLD_SEC = 5.0    # seconds of silence before "That's it sir?"
CONFIRMATION_TIMEOUT  = 10.0   # seconds to wait for "yes" confirmation
IVR_GREETING_TIMEOUT  = 30.0   # max seconds for initial greeting playback

# ── Department mappings ──────────────────────────────────────────────────────
VALID_DEPARTMENTS = [
    "Tech Department",
    "Billing Department",
    "Sales Department",
    "Support Department",
]

# ── Pre-synthesized phrases (saved to disk on startup) ───────────────────────
GREETING_PHRASE = (
    "Hello sir, can you tell me about your issues so that "
    "I can redirect your call to the most compatible department?"
)
CONFIRMATION_PHRASE = "That's it sir?"
FALLBACK_ROUTING_PHRASE = "I am routing your call to the Support Department."

# ── Cache directory for pre-rendered TTS ─────────────────────────────────────
CACHE_DIR = Path(__file__).parent / "tts_cache"
CACHE_DIR.mkdir(exist_ok=True)
