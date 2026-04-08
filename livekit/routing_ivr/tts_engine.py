# [ START: SYNTHESIS REQUEST ]
#       |
#       v
# +------------------------------------------+
# | synthesize(text)                         |
# | * Generate MD5 _cache_key()              |
# +------------------------------------------+
#       |
#       |----> [ Check In-Memory Cache ]
#       |      * Return bytes if found
#       |
#       |----> [ Check Disk Cache (.wav) ]
#       |      * Read file & update memory
#       |
#       | (If Cache Miss)
#       v
# +------------------------------------------+
# | Execute Piper TTS Process                |
# | * create_subprocess_exec()               |
# | * Input text -> Output Raw PCM           |
# +------------------------------------------+
#       |
#       |----> _raw_pcm_to_wav()
#       |      * Convert to 22050Hz Mono WAV
#       |
#       |----> [ Update Caches ]
#       |      * Write to Disk & Memory
#       v
# +------------------------------------------+
# | pre_warm()                               |
# | * Synthesize fixed phrases on startup    |
# | * (Greeting, Confirmation, Fallback)     |
# +------------------------------------------+
#       |
#       v
# [ END: READY FOR AUDIO PLAYBACK ]

import asyncio
import hashlib
import io
import logging
import wave
from pathlib import Path
from typing import Optional, Dict

from .config import (
    PIPER_EXE, PIPER_MODEL, ESPEAK_DATA, CACHE_DIR,
    GREETING_PHRASE, CONFIRMATION_PHRASE, FALLBACK_ROUTING_PHRASE,
)

logger = logging.getLogger("ivr.tts")

# In-memory cache: text -> wav bytes
_wav_cache: Dict[str, bytes] = {}


def _cache_key(text: str) -> str:
    logger.debug("Executing _cache_key")
    return hashlib.md5(text.encode()).hexdigest()


def _raw_pcm_to_wav(pcm: bytes, rate: int = 22050) -> bytes:
    logger.debug("Executing _raw_pcm_to_wav")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def synthesize(text: str, model_path: str = PIPER_MODEL) -> Optional[bytes]:
    """
    Run Piper TTS. Returns WAV bytes or None on failure.
    Uses cache to avoid re-synthesis of identical phrases.
    """
    logger.debug("Executing synthesize")
    key = _cache_key(text)

    # Check in-memory cache
    if key in _wav_cache:
        logger.debug("TTS cache hit: %s", text[:40])
        return _wav_cache[key]

    # Check disk cache
    disk_path = CACHE_DIR / f"{key}.wav"
    if disk_path.exists():
        wav = disk_path.read_bytes()
        _wav_cache[key] = wav
        return wav

    exe = Path(PIPER_EXE)
    model = Path(model_path)
    if not exe.exists():
        logger.error("Piper exe missing: %s", exe)
        return None
    if not model.exists():
        logger.error("Piper model missing: %s", model)
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            str(exe.absolute()),
            "--model", str(model.absolute()),
            "--output_raw",
            "--espeak_data", ESPEAK_DATA,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=text.encode("utf-8")),
            timeout=45.0,
        )
        if proc.returncode != 0:
            logger.error("Piper error: %s", stderr.decode())
            return None

        wav = _raw_pcm_to_wav(stdout)

        # Cache both in-memory and to disk
        _wav_cache[key] = wav
        disk_path.write_bytes(wav)
        logger.info("TTS synthesized + cached: %s", text[:50])
        return wav

    except asyncio.TimeoutError:
        logger.error("Piper TTS timed out")
        return None
    except Exception as e:
        logger.error("Piper TTS failed: %s", e)
        return None


async def pre_warm():
    """
    Pre-synthesize all fixed phrases on startup.
    These WAVs are saved to disk + memory — 0 CPU cost during calls.
    """
    logger.debug("Executing pre_warm")
    phrases = [
        GREETING_PHRASE,
        CONFIRMATION_PHRASE,
        FALLBACK_ROUTING_PHRASE,
    ]
    logger.info("Pre-warming %d TTS phrases...", len(phrases))
    for phrase in phrases:
        await synthesize(phrase)
    logger.info("TTS pre-warm complete")
