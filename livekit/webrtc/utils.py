# [ START: AUDIO DATA ]
#       |
#       |--- wav_bytes_to_pcm()
#       |    * Extract PCM and sample_rate
#       |    * Normalize and mono-mixdown
#       v
# +------------------------------------------+
# | AUDIO TRANSFORMATION                     |
# | * int16_to_float32() (Normalize)         |
# | * float32_to_int16() (Quantize)          |
# +------------------------------------------+
#       |
#       |----> resample_audio()
#       |      * Polyphase resampling (GCD-based)
#       |      * Adjust sample rate (e.g. to 16kHz)
#       v
# +------------------------------------------+
# | webrtc_time_base()                       |
# | * Return 1/48,000 Fraction               |
# +------------------------------------------+
#       |
#       v
# [ END: PROCESSED PCM / METADATA ]



import logging
logger = logging.getLogger(__name__)

import io
import wave
from fractions import Fraction
from math import gcd
from typing import Tuple

import numpy as np
from scipy.signal import resample_poly


def wav_bytes_to_pcm(wav_bytes: bytes) -> Tuple[np.ndarray, int]:
    logger.debug("Executing wav_bytes_to_pcm")
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        n_channels   = wf.getnchannels()
        sample_rate  = wf.getframerate()
        sample_width = wf.getsampwidth()
        n_frames     = wf.getnframes()
        raw          = wf.readframes(n_frames)

    if sample_width == 2:
        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        pcm = np.frombuffer(raw, dtype=np.float32).copy()
    else:
        raise ValueError(
            f"Unsupported WAV sample width: {sample_width} bytes "
            f"(expected 2 for int16 or 4 for float32)"
        )

    if n_channels == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.float32)
    elif n_channels > 2:
        pcm = pcm.reshape(-1, n_channels).mean(axis=1).astype(np.float32)

    return pcm, sample_rate


def resample_audio(pcm: np.ndarray, from_sr: int, to_sr: int) -> np.ndarray:
    logger.debug("Executing resample_audio")
    if from_sr == to_sr:
        return pcm.astype(np.float32)

    g    = gcd(from_sr, to_sr)
    up   = to_sr   // g
    down = from_sr // g
    resampled = resample_poly(pcm.astype(np.float64), up, down)
    return resampled.astype(np.float32)


def float32_to_int16(pcm: np.ndarray) -> np.ndarray:
    logger.debug("Executing float32_to_int16")
    clipped = np.clip(pcm, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16)


def int16_to_float32(pcm: np.ndarray) -> np.ndarray:
    logger.debug("Executing int16_to_float32")
    return pcm.astype(np.float32) / 32768.0


def webrtc_time_base() -> Fraction:
    logger.debug("Executing webrtc_time_base")
    return Fraction(1, 48_000)
