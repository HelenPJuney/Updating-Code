# [ START: EXTERNAL TRIGGER ]
#       |
#       |--- (A) POST /ai/join (Manual)
#       |--- (B) Webhook: PARTICIPANT_JOINED (Auto)
#       v
# +------------------------------------------+
# | ai_assist_router -> ai_join() / webhook()|
# | * Extract: room_id, mode, lang           |
# +------------------------------------------+
#       |
#       |----> ai_join_manager -> join_room()
#       |           |
#       |           v
#       |     +----------------------------------+
#       |     | Check self.active_sessions       |
#       |     | (Uses self._lock)                |
#       |     +----------------------------------+
#       |           |
#       |           |----> asyncio.create_task()
#       v           v
# [ RETURN 200 OK ]   [ BACKGROUND: _ai_assist_task() ]
# (API response)            |
#                           v
#             +----------------------------------+
#             | 1. generate_token()              |
#             | 2. Register Event Listeners      |
#             | 3. session.room.connect()        |
#             | 4. Publish AI Audio Track        |
#             +----------------------------------+
#                           |
#                           |----> Event: on("track_subscribed")
#                           v
#             +----------------------------------+
#             | _inbound_loop()                  |
#             | * Create rtc.AudioStream()       |
#             +----------------------------------+
#                           |
#                     [ AUDIO LOOP ]
#                           |
#             +----------------------------------+
#             | 1. Convert PCM (int16 -> f32)    |
#             | 2. resample_audio() (to 16kHz)   |
#             | 3. session.buf.push()            |
#             +----------------------------------+
#                           |
#               (If Buffer Ready & !Locked)
#                           |
#                           |----> asyncio.create_task()
#                           v
#             +----------------------------------+
#             | _process_utt()                   |
#             | * session.lock.acquire()         |
#             +----------------------------------+
#                           |
#                           |----> stt_sync() (Speech to Text)
#                           |----> _collapse_repetitions()
#                           |----> _is_hallucination()
#                           |----> _gemini_sync() (LLM Logic)
#                           |----> session.history.append()
#                           |
#                 (Check session.mode)
#                 /         |         \
#       [ ASSIST ]     [ PARALLEL ]    [ TAKEOVER ]
#           |               |               |
#     event_hub()     _piper_sync()   _piper_sync()
#     (Suggestion)    (TTS)           (TTS)
#           |               |               |
#           |         push_tts_wav()  push_tts_wav()
#           \               |               /
#            +--------------+--------------+
#                           |
#             +----------------------------------+
#             | session.lock.release()           |
#             +----------------------------------+
#                           |
#               [ WAIT FOR NEXT AUDIO ]
              
              
import asyncio
import io
import json
import logging
import uuid
import numpy as np

from typing import Dict, Any, Optional

try:
    from livekit import rtc
except ImportError:
    rtc = None
from livekit.token_service import generate_token, LIVEKIT_URL
from livekit.websocket import event_hub
from livekit.audio_source import TtsAudioSource

try:
    from backend.core.stt import stt_sync, _collapse_repetitions, _is_hallucination
    from backend.core.tts import _http_tts_sync as _piper_sync, _humanize_text
    from backend.core.llm import _gemini_sync
    from backend.core.config import LANGUAGE_CONFIG
except ImportError:
    stt_sync = _collapse_repetitions = _is_hallucination = None
    _piper_sync = _humanize_text = _gemini_sync = LANGUAGE_CONFIG = None

from livekit.ai_assist.ai_modes import AIMode

logger = logging.getLogger("callcenter.ai_assist.join_manager")

class BufList:
    def __init__(self):
        self.frames = []
    def push(self, frame):
        self.frames.append(frame)
    def ready(self):
        # A simple heuristic for 500ms chunk
        return len(self.frames) > 50
    def flush(self):
        if not self.frames: return None
        out = np.concatenate(self.frames)
        self.frames = []
        return out

class AIAssistSession:
    def __init__(self, room_id: str, mode: AIMode, lang: str):
        self.room_id = room_id
        self.session_id = str(uuid.uuid4())
        self.mode = mode
        self.lang = lang  # Dynamic language
        self.room = rtc.Room()
        self.audio_source = TtsAudioSource()
        self.history = []
        self.buf = BufList()
        self.lock = asyncio.Lock()
        self.closed = False
        self.identity = f"ai-assist-{self.session_id[:8]}"

class AIJoinManager:
    def __init__(self):
        self.active_sessions: Dict[str, AIAssistSession] = {}
        self._lock = asyncio.Lock() 
       
        self._join_debounce: Dict[str, float] = {}
        self._debounce_window: float = 2.0  # seconds

    async def join_room(self, room_id: str, mode: str, lang: str = "en", source: str = "browser"):
      
        import time as _t
        mode_enum = AIMode(mode)
       
        async with self._lock:
            
            last_attempt = self._join_debounce.get(room_id, 0.0)
            now = _t.time()
            if now - last_attempt < self._debounce_window:
                logger.debug(
                    "[AI Assist] debounce — skipping duplicate join attempt  "
                    "room=%s  source=%s  (%.2fs since last attempt)",
                    room_id, source, now - last_attempt,
                )
                return
            self._join_debounce[room_id] = now

            if room_id in self.active_sessions:
                logger.warning(f"[AI Assist] AI already joined/joining room {room_id} (source={source})")
                return
            session = AIAssistSession(room_id, mode_enum, lang)
            self.active_sessions[room_id] = session
        
        logger.info(
            "[AI Assist] joining room=%s  mode=%s  lang=%s  source=%s",
            room_id, mode, lang, source,
        )
      
        asyncio.create_task(self._ai_assist_task(session, source))

    async def _ai_assist_task(self, session: AIAssistSession, source: str = "browser"):
        token = generate_token(
            room_name=session.room_id,
            identity=session.identity,
            name="AI Assistant",
            can_publish=True,
            can_subscribe=True,
        )

        @session.room.on("participant_connected")
        def _on_p_connected(participant):
            logger.info(f"[AI Assist] Participant {getattr(participant, 'identity', '')} joined {session.room_id}")

        @session.room.on("participant_disconnected")
        def _on_p_disconnected(participant):
            
            remaining = [
                p for p in session.room.remote_participants.values()
                if "ai-assist" not in (getattr(p, "identity", "") or "")
            ]
            if not remaining:
                logger.info(f"[AI Assist] Room {session.room_id} is empty. Disconnecting AI.")
                session.closed = True

        @session.room.on("track_subscribed")
        def _on_t_subscribed(track, publication, participant):
            is_audio = isinstance(track, rtc.RemoteAudioTrack)
            if not is_audio:
                kind_val = getattr(track, "kind", None)
                is_audio = (kind_val == 1 or kind_val == getattr(rtc, "TrackKind", None) and getattr(rtc.TrackKind, "KIND_AUDIO", 1) == 1)
            
            if is_audio:
                logger.info(f"[AI Assist] Subscribed to track from {getattr(participant, 'identity', '')}")
                asyncio.create_task(self._inbound_loop(session, track))

        @session.room.on("disconnected")
        def _on_disconnected(*args):
            logger.info(f"[AI Assist] AI Disconnected from room {session.room_id}")
            session.closed = True

        # Connect to LiveKit
        try:
            await session.room.connect(LIVEKIT_URL, token)
            session.audio_source.start()
            ai_track = rtc.LocalAudioTrack.create_audio_track("ai-voice", session.audio_source.source)
            opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await session.room.local_participant.publish_track(ai_track, opts)
            
            # Notify WS clients
            asyncio.create_task(event_hub.publish({
                "type": "ai_joined",
                "room_id": session.room_id,
                "mode": session.mode.value,
                "lang": session.lang
            }))
            
            if session.mode == AIMode.TAKEOVER:
                asyncio.create_task(event_hub.publish({
                    "type": "ai_takeover",
                    "room_id": session.room_id
                }))
        except Exception as e:
            logger.error(f"[AI Assist] Failed to connect: {e}")
            async with self._lock:
                if session.room_id in self.active_sessions:
                    del self.active_sessions[session.room_id]
            return

        while not session.closed:
            await asyncio.sleep(1)

        # Cleanup
        try:
            await asyncio.wait_for(session.room.disconnect(), timeout=5.0)
        except Exception as e:
            logger.warning(f"[AI Assist] Error disconnecting from room {session.room_id}: {e}")
            
        async with self._lock:
            if session.room_id in self.active_sessions:
                del self.active_sessions[session.room_id]
                logger.info(f"[AI Assist] Cleaned up session memory for {session.room_id}")
            if session.room_id in self._join_debounce:
                del self._join_debounce[session.room_id]
        
    async def _inbound_loop(self, session: AIAssistSession, track):
        try:
            stream = rtc.AudioStream(track, sample_rate=16_000, num_channels=1)
        except TypeError:
            stream = rtc.AudioStream(track)
            
        async for event in stream:
            if session.closed: break
            
            frame = getattr(event, "frame", event)
            raw = getattr(frame, "data", None)
            if raw is None: continue
            
            pcm_int16 = np.frombuffer(bytes(raw), dtype=np.int16)
            pcm_f32 = pcm_int16.astype(np.float32) / 32768.0
            
            sr = getattr(frame, "sample_rate", 16_000)
            if sr != 16_000:
                from backend.webrtc.utils import resample_audio
                pcm_f32 = resample_audio(pcm_f32, sr, 16_000)
                
            session.buf.push(pcm_f32)
            
            if session.buf.ready() and not session.lock.locked():
                pcm_utt = session.buf.flush()
                if pcm_utt is not None:
                    asyncio.create_task(self._process_utt(pcm_utt, session))

    async def _process_utt(self, pcm: np.ndarray, session: AIAssistSession):
        loop = asyncio.get_running_loop()
        async with session.lock:
            try:
                # Used dynamic STT language instead of hardcoded 'en'
                user_text: str = await loop.run_in_executor(None, stt_sync, pcm, session.lang)
            except Exception as e:
                logger.error(f"[AI Assist] STT Error: {e}")
                return

            if not user_text: return
            user_text = _collapse_repetitions(user_text)
            if _is_hallucination(user_text): return

            session.history.append({"role": "user", "text": user_text})
            hist_snap = list(session.history)

            try:
                ai_text: str = await loop.run_in_executor(
                    None, _gemini_sync, hist_snap, session.lang, "neutral"
                )
            except Exception as e:
                logger.error(f"[AI Assist] LLM Error: {e}")
                return

            if not ai_text: return
            session.history.append({"role": "assistant", "text": ai_text})

            # Handle behaviors based on mode
            if session.mode == AIMode.ASSIST:
                asyncio.create_task(event_hub.publish({
                    "type": "ai_suggestion",
                    "room_id": session.room_id,
                    "suggestion": ai_text,
                    "user_text": user_text
                }))

            elif session.mode in [AIMode.PARALLEL, AIMode.TAKEOVER]:
                # Send to WS also
                asyncio.create_task(event_hub.publish({
                    "type": "ai_response",
                    "room_id": session.room_id,
                    "text": ai_text,
                    "user_text": user_text
                }))
                # TTS
                try:
                    wav_bytes = await loop.run_in_executor(None, _piper_sync, ai_text, "")
                    await session.audio_source.push_tts_wav(wav_bytes)
                except Exception as e:
                    logger.error(f"[AI Assist] TTS Error: {e}")

ai_join_manager = AIJoinManager()
