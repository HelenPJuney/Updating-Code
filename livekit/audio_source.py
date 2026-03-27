
# [ START ]
#     |
#     v
# +--------------------------+
# | TtsAudioSource()         |
# | * initialize rtc.Source  |
# +--------------------------+
#     |
#     |----> asyncio.Queue() * init _queue
#     |
#     v
# +--------------------------+
# | start()                  |
# | * launch background task |
# +--------------------------+
#     |
#     |----> asyncio.ensure_future() -> _pump()
#     |
#     v
# +--------------------------+
# | push_tts_wav()           |
# | * accept Piper audio     |
# +--------------------------+
#     |
#     |----> wav_bytes_to_pcm()
#     |
#     |----> resample_audio() * to 48kHz
#     |
#     |----> float32_to_int16()
#     |
#     |----> self._queue.put_nowait() * slice into 20ms chunks
#     |
#     v
# +--------------------------+
# | _pump()                  |
# | * continuous loop        |
# +--------------------------+
#     |
#     |----> self._queue.get()
#     |
#     |----> <rtc.AudioFrame> -> init()
#     |
#     |----> <rtc.AudioSource> -> capture_frame()
#     |
#     v
# +--------------------------+
# | clear()                  |
# | * barge-in handler       |
# +--------------------------+
#     |
#     |----> self._queue.get_nowait() * drain until empty
#     |
#     |----> return drained_count
#     |
#     v
# +--------------------------+
# | stop()                   |
# | * cleanup session        |
# +--------------------------+
#     |
#     |----> self._task.cancel()
#     |
# [ END ]

import asyncio
import logging
from typing import Optional

import numpy as np

from backend.webrtc.utils import wav_bytes_to_pcm, resample_audio, float32_to_int16

logger = logging.getLogger("callcenter.livekit.audio_source")

# ── Constants ─────────────────────────────────────────────────────────────────
_SR            = 48_000       # LiveKit / WebRTC output sample rate (Hz)
_FRAME_SAMPLES = 960          # 20 ms per frame at 48 kHz (standard Opus)
_MAX_QUEUE     = 500          # ≈ 10 s of buffered audio

class TtsAudioSource:

    def __init__(self) -> None:
        from livekit import rtc
        # The LiveKit audio source that the published track reads from
        self.source: "rtc.AudioSource" = rtc.AudioSource(
            sample_rate=_SR,
            num_channels=1,
        )
        self._queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._closed: bool = False
        self._task:   Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    # --------------------------------------------------
    # start -> Launch the _pump coroutine (call after event loop running)
    # --------------------------------------------------
    def start(self) -> None:
        """Start the background pump. Call once after the room is connected."""
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._pump())

    # --------------------------------------------------
    # stop -> Signal pump to exit; cancel task
    # --------------------------------------------------
    def stop(self) -> None:
        """Stop the pump coroutine on session close."""
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()

    # ── Internal pump ─────────────────────────────────────────────────────────

    async def _pump(self) -> None:
  
        from livekit import rtc

        while not self._closed:
            try:
                chunk: np.ndarray = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue   # keep looping — session might still be alive
            except asyncio.CancelledError:
                break
            except Exception:
                break

            frame = rtc.AudioFrame(
                data               = chunk.tobytes(),
                sample_rate        = _SR,
                num_channels       = 1,
                samples_per_channel= _FRAME_SAMPLES,
            )
            try:
                await self.source.capture_frame(frame)
            except Exception:
                # Room closed or source released — stop pumping
                logger.debug("[TtsAudioSource] capture_frame error — pump stopping")
                break

    # ── TTS integration ───────────────────────────────────────────────────────

    async def push_tts_wav(self, wav_bytes: bytes) -> None:
     
        try:
            pcm_f32, native_sr = wav_bytes_to_pcm(wav_bytes)
        except Exception:
            logger.exception("[TtsAudioSource] Failed to decode WAV bytes — skipping")
            return

        if native_sr != _SR:
            pcm_f32 = resample_audio(pcm_f32, native_sr, _SR)

        pcm_i16 = float32_to_int16(pcm_f32)

        for i in range(0, len(pcm_i16), _FRAME_SAMPLES):
            chunk = pcm_i16[i : i + _FRAME_SAMPLES]

            # Zero-pad the final partial frame
            if len(chunk) < _FRAME_SAMPLES:
                chunk = np.pad(chunk, (0, _FRAME_SAMPLES - len(chunk)))

            if self._queue.full():
                try:
                    self._queue.get_nowait()   # drop oldest frame to make room
                except asyncio.QueueEmpty:
                    pass
                logger.debug("[TtsAudioSource] queue overflow — oldest frame dropped")

            try:
                self._queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass

    # ── Barge-in support ──────────────────────────────────────────────────────

    def clear(self) -> int:
      
        drained = 0
        while True:
            try:
                self._queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.debug("[TtsAudioSource] cleared %d frames on barge-in", drained)
        return drained
