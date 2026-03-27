# [ START: LiveKit Webhook Events ]
#     |
#     +-----> on_room_started() / on_track_published() [ Logs Only ]
#     |
#     v
# +-------------------------------------------------+
# | on_participant_joined()                         |
# | * Main entry point for both Caller and AI       |
# +-------------------------------------------------+
#     |
#     |--- (If SIP Caller Joins)
#     |      |
#     |      |----> sip_session_mgr.register()
#     |      |----> _start_ringing_timeout() ----------+--> _ringing_watchdog() [If AI doesn't join]
#     |      |                                         |      |--> sip_session_mgr.mark_failed()
#     |      v                                         |      |--> _publish_sip_call_failed()
#     |  _publish_sip_call_request()                   |      +--> _delayed_room_cleanup()
#     |      |                                         |
#     |      +--> [If Kafka fails]                     |
#     |             |--> _fallback_direct_spawn()      |
#     |                                                |
#     |--- (If AI Worker Joins)                        |
#            |                                         |
#            |----> sip_session_mgr.mark_connected()   |
#            |----> _cancel_timeout() <----------------+ [Stops ringing watchdog]
#            |----> _start_call_timeout() -------------+
#                                                      |
#                                                      v
# [ ACTIVE CALL IN PROGRESS ]                  _call_watchdog() [If call hits max duration]
#     |                                                |
#     v                                                |--> sip_session_mgr.mark_completed()
# +-------------------------------------------------+  |--> _publish_sip_call_completed()
# | on_participant_left()                           |  +--> _delayed_room_cleanup()
# | * Triggered when SIP caller hangs up            |  |
# +-------------------------------------------------+  |
#     |                                                |
#     |----> _cancel_timeout() <-----------------------+ [Stops max duration watchdog]
#     |----> sip_session_mgr.mark_completed()
#     |----> _publish_sip_call_completed()
#     |----> _trigger_recording_save()
#     |
#     v
# +-------------------------------------------------+
# | _delayed_room_cleanup()                         |
# | * Waits 2 seconds, then destroys room           |
# +-------------------------------------------------+
#     |
#     |----> LiveKitAPI.delete_room()
#     |----> sip_session_mgr.remove()
#     |
#     v
# +-------------------------------------------------+
# | on_room_finished()                              |
# | * Final safety net if LiveKit destroys room     |
# +-------------------------------------------------+
#     |
#     |----> _cancel_timeout()
#     |----> sip_session_mgr.mark_completed()
#     |----> sip_session_mgr.remove()
#     |
# [ END ]

import asyncio
import logging
import time
import uuid
from typing import Optional

from .sip_config import (
    SIP_PARTICIPANT_PREFIX,
    SIP_DEFAULT_LANG,
    SIP_DEFAULT_LLM,
    SIP_DEFAULT_VOICE,
    SIP_DEFAULT_AGENT_NAME,
    SIP_RETRY_MAX,
    SIP_RETRY_DELAY_SEC,
    SIP_CALL_TIMEOUT_SEC,
    SIP_RINGING_TIMEOUT_SEC,
)
from .sip_session_manager import (
    SipSession,
    SipCallState,
    sip_session_mgr,
)

logger = logging.getLogger("callcenter.sip.event_handler")

# Track active timeout tasks so we can cancel them
_timeout_tasks: dict[str, asyncio.Task] = {}   # session_id → timeout Task


class SipEventHandler:
    """
    Stateless event handler — processes LiveKit webhook payloads and triggers
    Kafka-based call scheduling for SIP calls.

    Thread-safety: all state mutations go through SipSessionManager which
    is asyncio.Lock-protected.
    """

    # ── Room events ───────────────────────────────────────────────────────────

    @staticmethod
    async def on_room_started(room_name: str, room_sid: str) -> None:
        """
        Called when LiveKit creates a new room (possibly from SIP INVITE).
        We only log here — the actual session creation happens when the SIP
        participant joins, because that's when we have caller identity info.
        """
        logger.info(
            "[SipHandler] room_started  room=%s  sid=%s",
            room_name[:12], room_sid[:12],
        )

    @staticmethod
    async def on_participant_joined(
        room_name: str,
        room_sid: str,
        participant_identity: str,
        participant_sid: str,
        participant_metadata: str = "",
    ) -> Optional[SipSession]:
      
        # Only handle SIP participants
        if not participant_identity.startswith(SIP_PARTICIPANT_PREFIX):
            logger.debug(
                "[SipHandler] non-SIP participant joined  identity=%s  room=%s",
                participant_identity[:16], room_name[:12],
            )
            # If this is an AI worker joining a SIP room, mark connected
            sip_session = sip_session_mgr.get_by_room(room_name)
            if sip_session and sip_session.state == SipCallState.RINGING:
                await sip_session_mgr.mark_connected(sip_session.session_id)
                # Cancel ringing timeout
                _cancel_timeout(sip_session.session_id)
                # Start max call duration timeout
                _start_call_timeout(sip_session)
                logger.info(
                    "[SipHandler] AI worker joined SIP room → connected  session=%s",
                    sip_session.session_id[:8],
                )
            return None

        # Check if already registered (idempotent for inbound, completes connection for outbound)
        existing = sip_session_mgr.get_by_room(room_name)
        if existing:
            logger.debug(
                "[SipHandler] SIP session already exists  room=%s  session=%s",
                room_name[:12], existing.session_id[:8],
            )
            # For outbound calls, when the PSTN user joins, ensure we mark connected
            if existing.state == SipCallState.RINGING:
                await sip_session_mgr.mark_connected(existing.session_id)
                _cancel_timeout(existing.session_id)
                _start_call_timeout(existing)
                logger.info("[SipHandler] outbound SIP caller joined → connected session=%s", existing.session_id[:8])
            return existing


        caller_number = participant_identity.removeprefix(SIP_PARTICIPANT_PREFIX)

        # Generate internal identifiers
        session_id = str(uuid.uuid4())
        # SIP call_id: use participant SID from LiveKit as the canonical SIP ID
        sip_call_id = participant_sid or str(uuid.uuid4())

        # Register session mapping
        sip_session = await sip_session_mgr.register(
            sip_call_id=sip_call_id,
            session_id=session_id,
            room_id=room_name,
            caller_number=caller_number,
            participant_id=participant_identity,
        )

        # Publish CallRequest to Kafka (same path as /livekit/token)
        success = await _publish_sip_call_request(
            session_id=session_id,
            room_id=room_name,
            caller_number=caller_number,
        )

        if not success:
            _cancel_timeout(session_id)
            await sip_session_mgr.mark_failed(session_id)
            await _publish_sip_call_failed(sip_session, "dispatch_failed")
            asyncio.ensure_future(_delayed_room_cleanup(sip_session))
            return None

        # Start ringing timeout — if no AI worker joins within N seconds, fail
        _start_ringing_timeout(sip_session)

        logger.info(
            "[SipHandler] SIP call initiated  caller=%s  session=%s  room=%s",
            caller_number, session_id[:8], room_name[:12],
        )
        return sip_session

    @staticmethod
    async def on_track_published(
        room_name: str,
        participant_identity: str,
        track_sid: str,
        track_type: str,
    ) -> None:
        """
        Called when a track is published in a room.
        For SIP calls, the first audio track indicates media is flowing.
        """
        if not participant_identity.startswith(SIP_PARTICIPANT_PREFIX):
            return

        sip_session = sip_session_mgr.get_by_room(room_name)
        if not sip_session:
            return

        logger.info(
            "[SipHandler] SIP track published  type=%s  session=%s  track=%s",
            track_type, sip_session.session_id[:8], track_sid[:12],
        )

    @staticmethod
    async def on_participant_left(
        room_name: str,
        participant_identity: str,
        participant_sid: str,
    ) -> None:
        """
        Called when a participant leaves a LiveKit room.

        If the SIP caller leaves → mark session completed, publish
        call_completed to Kafka, trigger IVR recording save, and schedule
        room cleanup.
        """
        if not participant_identity.startswith(SIP_PARTICIPANT_PREFIX):
            return

        sip_session = sip_session_mgr.get_by_room(room_name)
        if not sip_session:
            logger.warning(
                "[SipHandler] SIP participant left but no session found  room=%s",
                room_name[:12],
            )
            return

        if sip_session.state in (SipCallState.COMPLETED, SipCallState.FAILED):
            return  # already cleaned up

        # Cancel any pending timeouts
        _cancel_timeout(sip_session.session_id)

        # Calculate duration
        duration_sec = time.time() - sip_session.created_at

        # Mark completed
        await sip_session_mgr.mark_completed(sip_session.session_id)

        # Publish call_completed event to Kafka
        await _publish_sip_call_completed(sip_session, duration_sec)

        # Trigger IVR recording save (if applicable)
        await _trigger_recording_save(sip_session, duration_sec)

        # Schedule room cleanup (slight delay to let AI worker teardown gracefully)
        asyncio.ensure_future(_delayed_room_cleanup(sip_session, delay_sec=2.0))

        logger.info(
            "[SipHandler] SIP caller left  session=%s  duration=%.0fs",
            sip_session.session_id[:8], duration_sec,
        )

    @staticmethod
    async def on_room_finished(room_name: str, room_sid: str) -> None:
        """
        Called when a LiveKit room is destroyed.
        Final cleanup of any remaining SIP session state.
        """
        sip_session = sip_session_mgr.get_by_room(room_name)
        if not sip_session:
            return

        # Cancel any pending timeouts
        _cancel_timeout(sip_session.session_id)

        if sip_session.state not in (SipCallState.COMPLETED, SipCallState.FAILED):
            await sip_session_mgr.mark_completed(sip_session.session_id)

        await sip_session_mgr.remove(sip_session.session_id)

        logger.info(
            "[SipHandler] room_finished cleanup  session=%s  room=%s",
            sip_session.session_id[:8], room_name[:12],
        )


# ══════════════════════════════════════════════════════════════════════════════
# Timeout Management
# ══════════════════════════════════════════════════════════════════════════════

def _start_ringing_timeout(sip_session: SipSession) -> None:
    """Start a watchdog that fails the call if no AI worker joins in time."""
    async def _ringing_watchdog():
        await asyncio.sleep(SIP_RINGING_TIMEOUT_SEC)
        sess = sip_session_mgr.get_by_session(sip_session.session_id)
        if sess and sess.state == SipCallState.RINGING:
            logger.warning(
                "[SipTimeout] ringing timeout after %ds  session=%s",
                SIP_RINGING_TIMEOUT_SEC, sess.session_id[:8],
            )
            await sip_session_mgr.mark_failed(sess.session_id)
            await _publish_sip_call_failed(sess, "ringing_timeout")
            await _delayed_room_cleanup(sess, delay_sec=1.0)

    task = asyncio.ensure_future(_ringing_watchdog())
    _timeout_tasks[sip_session.session_id] = task


def _start_call_timeout(sip_session: SipSession) -> None:
    """Start max-duration watchdog that force-ends long calls."""
    async def _call_watchdog():
        await asyncio.sleep(SIP_CALL_TIMEOUT_SEC)
        sess = sip_session_mgr.get_by_session(sip_session.session_id)
        if sess and sess.state == SipCallState.CONNECTED:
            logger.warning(
                "[SipTimeout] max call duration reached (%ds)  session=%s",
                SIP_CALL_TIMEOUT_SEC, sess.session_id[:8],
            )
            await sip_session_mgr.mark_completed(sess.session_id)
            duration = time.time() - sess.created_at
            await _publish_sip_call_completed(sess, duration)
            await _delayed_room_cleanup(sess, delay_sec=2.0)

    task = asyncio.ensure_future(_call_watchdog())
    _timeout_tasks[sip_session.session_id] = task


def _cancel_timeout(session_id: str) -> None:
    """Cancel any pending timeout task for a session."""
    task = _timeout_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


# ══════════════════════════════════════════════════════════════════════════════
# Kafka Integration Helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _publish_sip_call_request(
    session_id: str,
    room_id: str,
    caller_number: str,
    lang: str = SIP_DEFAULT_LANG,
    llm: str = SIP_DEFAULT_LLM,
    voice: str = SIP_DEFAULT_VOICE,
    agent_name: str = SIP_DEFAULT_AGENT_NAME,
    source: str = "sip",
) -> bool:
    """
    Publish a CallRequest to Kafka for a SIP-originated call.

    Uses the existing CallRequestProducer — same path as the /livekit/token
    endpoint, but without needing a browser HTTP request.

    Retries on failure with exponential backoff.
    Falls back to direct ai_worker_task spawn if Kafka is unavailable.
    """
    from ..kafka.producer import get_producer
    from ..kafka.schemas import CallRequest

    producer = get_producer()

    req = CallRequest(
        session_id=session_id,
        room_id=room_id,
        lang=lang,
        llm=llm,
        voice=voice,
        model_path="",
        agent_name=agent_name,
        source=source,
        caller_number=caller_number,
    )

    last_exc: Optional[Exception] = None
    for attempt in range(SIP_RETRY_MAX):
        try:
            if producer.is_kafka_active:
                result = await producer.submit_call_request(req)
                if result is not None:
                    logger.info(
                        "[SipBridge] CallRequest published to Kafka  session=%s  room=%s",
                        session_id[:8], room_id[:8],
                    )
                    return True
                else:
                    # Kafka returned None — use fallback direct spawn
                    await _fallback_direct_spawn(req)
                    return True
            else:
                # Kafka not active — fallback to direct spawn
                await _fallback_direct_spawn(req)
                return True

        except Exception as exc:
            last_exc = exc
            delay = SIP_RETRY_DELAY_SEC * (2 ** attempt)
            logger.warning(
                "[SipBridge] Kafka publish failed (attempt %d/%d): %s  retrying in %.1fs",
                attempt + 1, SIP_RETRY_MAX, exc, delay,
            )
            await asyncio.sleep(delay)

    # All retries exhausted — last resort fallback
    logger.error(
        "[SipBridge] Failed to publish CallRequest after %d retries  session=%s  error=%s",
        SIP_RETRY_MAX, session_id[:8], last_exc,
    )

    try:
        await _fallback_direct_spawn(req)
        return True
    except Exception:
        logger.exception(
            "[SipBridge] Fallback spawn also failed  session=%s", session_id[:8]
        )
        await sip_session_mgr.mark_failed(session_id)
        return False


async def _fallback_direct_spawn(req) -> None:
    """
    When Kafka is unavailable, spawn ai_worker_task directly.
    Same fallback path as the /livekit/token endpoint.
    """
    try:
        from ..ai_worker import ai_worker_task
        asyncio.ensure_future(
            ai_worker_task(
                room_id=req.room_id,
                session_id=req.session_id,
                lang=req.lang,
                llm_key=req.llm,
                voice_stem=req.voice,
                model_path=req.model_path,
                agent_name=req.agent_name,
            )
        )
        logger.info(
            "[SipBridge] fallback direct spawn  session=%s  room=%s",
            req.session_id[:8], req.room_id[:8],
        )
    except ImportError:
        logger.error("[SipBridge] ai_worker_task not importable — cannot spawn worker")
        raise


async def _publish_sip_call_completed(sip_session: SipSession, duration_sec: float) -> None:
    """
    Publish a call_completed event for a SIP call that ended.
    Mirrors WorkerService._publish_completed.
    """
    from ..kafka.producer import get_producer
    from ..kafka.schemas import CallCompleted
    from ..kafka.config import TOPIC_CALL_COMPLETED, NODE_ID

    producer = get_producer()
    if not producer.is_kafka_active:
        logger.debug("[SipBridge] skipping call_completed publish (Kafka inactive)")
        return

    try:
        from aiokafka import AIOKafkaProducer
    except ImportError:
        return

    evt = CallCompleted(
        session_id=sip_session.session_id,
        room_id=sip_session.room_id,
        node_id=NODE_ID,
        duration_sec=duration_sec,
    )

    try:
        internal = getattr(producer, '_producer', None)
        if internal:
            await internal.send_and_wait(
                TOPIC_CALL_COMPLETED,
                value=evt.model_dump_json().encode("utf-8"),
                key=sip_session.session_id.encode("utf-8"),
            )
            logger.info(
                "[SipBridge] call_completed published  session=%s  duration=%.0fs",
                sip_session.session_id[:8], duration_sec,
            )
    except Exception as exc:
        logger.warning("[SipBridge] call_completed publish failed: %s", exc)


async def _publish_sip_call_failed(sip_session: SipSession, reason: str) -> None:
    """
    Publish a call_failed event for a SIP call that could not be established.
    """
    from ..kafka.producer import get_producer
    from ..kafka.schemas import CallFailed
    from ..kafka.config import TOPIC_CALL_FAILED, NODE_ID

    producer = get_producer()
    if not producer.is_kafka_active:
        return

    try:
        from aiokafka import AIOKafkaProducer
    except ImportError:
        return

    evt = CallFailed(
        session_id=sip_session.session_id,
        room_id=sip_session.room_id,
        node_id=NODE_ID,
        error=reason,
    )

    try:
        internal = getattr(producer, '_producer', None)
        if internal:
            await internal.send_and_wait(
                TOPIC_CALL_FAILED,
                value=evt.model_dump_json().encode("utf-8"),
                key=sip_session.session_id.encode("utf-8"),
            )
            logger.info(
                "[SipBridge] call_failed published  session=%s  reason=%s",
                sip_session.session_id[:8], reason,
            )
    except Exception as exc:
        logger.warning("[SipBridge] call_failed publish failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# IVR Recording Save
# ══════════════════════════════════════════════════════════════════════════════

async def _trigger_recording_save(sip_session: SipSession, duration_sec: float) -> None:
    """
    Trigger saving the IVR recording for a completed SIP call.

    The ai_worker_task already saves recordings as part of its lifecycle.
    This hook exists to trigger any ADDITIONAL recording persistence needed
    specifically for SIP calls (e.g., CDR log, external API notification).

    The actual audio recording is handled by the AI worker which joins the
    LiveKit room and captures audio via its existing pipeline.
    """
    logger.info(
        "[SipRecording] call ended  session=%s  caller=%s  duration=%.0fs",
        sip_session.session_id[:8],
        sip_session.caller_number,
        duration_sec,
    )

    # CDR (Call Detail Record) — log for analytics / billing
    cdr = {
        "sip_call_id": sip_session.sip_call_id,
        "session_id": sip_session.session_id,
        "room_id": sip_session.room_id,
        "caller_number": sip_session.caller_number,
        "duration_sec": round(duration_sec, 1),
        "source": "sip",
        "state": sip_session.state.value,
        "started_at": sip_session.created_at,
        "ended_at": time.time(),
    }

    # Publish CDR to Kafka sip_events topic for downstream analytics
    try:
        from ..kafka.producer import get_producer
        from ..kafka.config import TOPIC_SIP_EVENTS

        producer = get_producer()
        if producer.is_kafka_active:
            import json
            internal = getattr(producer, '_producer', None)
            if internal:
                await internal.send_and_wait(
                    TOPIC_SIP_EVENTS,
                    value=json.dumps(cdr).encode("utf-8"),
                    key=sip_session.session_id.encode("utf-8"),
                )
                logger.info(
                    "[SipRecording] CDR published to sip_events  session=%s",
                    sip_session.session_id[:8],
                )
    except Exception as exc:
        logger.debug("[SipRecording] CDR publish failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# Room Cleanup
# ══════════════════════════════════════════════════════════════════════════════

async def _delayed_room_cleanup(sip_session: SipSession, delay_sec: float = 2.0) -> None:
    """
    After a short delay, clean up the LiveKit room for the SIP call.
    The delay allows the AI worker to finish its teardown gracefully.
    """
    await asyncio.sleep(delay_sec)

    try:
        from livekit.api import LiveKitAPI, DeleteRoomRequest
        from ..token_service import LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET

        api = LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        try:
            await api.room.delete_room(
                DeleteRoomRequest(room=sip_session.room_id)
            )
            logger.info(
                "[SipCleanup] room deleted  room=%s  session=%s",
                sip_session.room_id[:8], sip_session.session_id[:8],
            )
        finally:
            await api.aclose()
    except ImportError:
        logger.debug("[SipCleanup] livekit-api not available — room cleanup skipped")
    except Exception as exc:
        logger.debug("[SipCleanup] room cleanup error: %s", exc)
    finally:
        # Always remove from session manager
        _cancel_timeout(sip_session.session_id)
        await sip_session_mgr.remove(sip_session.session_id)
