# [ START: CALL CENTER INTERACTION ]
#       |
#       |--- (A) POST /call (New Caller Entry)
#       |--- (B) POST /agent/online | /offline
#       |--- (C) POST /agent/accept/{session_id}
#       |--- (D) POST /outbound/accept | /decline
#       v
# +------------------------------------------+
# | CALLER ENTRY: start_call()               |
# | * Check business_hours.should_reject()   |
# | * db.upsert_user()                       |
# | * generate_token() for room              |
# | * queue_engine.enqueue_caller()          |
# +------------------------------------------+
#       |
#       |----> [ Caller Disconnect ]
#       |      * disconnect_call() -> dequeue_caller()
#       v
# +------------------------------------------+
# | AGENT MANAGEMENT                         |
# | * agent_go_online(): Set 'online' status |
# | * agent_accept_call():                   |
# |   1. queue_engine.pop_caller()           |
# |   2. db.set_agent_busy()                 |
# |   3. Kick 'piper-tts' from room          |
# |   4. generate_token() for Agent          |
# | * agent_end_call():                      |
# |   1. Calculate call duration             |
# |   2. db.set_agent_free()                 |
# +------------------------------------------+
#       |
#       v
# +------------------------------------------+
# | OUTBOUND CALLBACK LOGIC                  |
# | * outbound_accept():                     |
# |   1. db.complete_outbound()              |
# |   2. event_hub.publish('caller_pickup')  |
# | * outbound_no_answer():                  |
# |   1. Mark status 'no_answer'             |
# |   2. send_outbound_no_answer_email()     |
# | * outbound_decline():                    |
# |   1. db.set_agent_ignoring_outbounds()   |
# +------------------------------------------+
#       |
#       v
# [ END: LOGS UPDATED / SESSION CLOSED ]

import os
import time
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from . import db
from . import queue_engine
from . import business_hours
from .email_service import send_outbound_no_answer_email
from ..token_service import generate_token, LIVEKIT_URL

logger = logging.getLogger("callcenter.api")

cc_router = APIRouter(prefix="/cc", tags=["callcenter"])


# ═══════════════════════════════════════════════════════════════════════════════
# Request / Response Models
# ═══════════════════════════════════════════════════════════════════════════════

class AuthRequest(BaseModel):
    email: str

class CallRequest(BaseModel):
    email: str
    department: str = "General Support"
    skip_outbound: bool = False

class AgentOnlineRequest(BaseModel):
    agent_identity: str
    agent_name: str
    department: str

class AgentOfflineRequest(BaseModel):
    agent_identity: str

class AgentEndCallRequest(BaseModel):
    agent_identity: str
    session_id: str

class OutboundAcceptRequest(BaseModel):
    outbound_id: int
    agent_identity: str

class OutboundCompleteRequest(BaseModel):
    outbound_id: int
    agent_identity: str

class OutboundNoAnswerRequest(BaseModel):
    outbound_id: int
    user_email: str
    department: str
    agent_identity: str

class OutboundDeclineRequest(BaseModel):
    outbound_id: int
    agent_identity: str
    reason: str = ""
    snooze_minutes: int = 10

class OutboundResumeRequest(BaseModel):
    agent_identity: str

class HolidayRequest(BaseModel):
    message: str
    until: str  # ISO datetime string

class AdminConfigRequest(BaseModel):
    key: str
    value: str

class BusinessHoursRequest(BaseModel):
    work_start: str   # "HH:MM"
    work_end: str     # "HH:MM"
    work_days: str    # comma-separated ints "0,1,2,3,4,5"
    timezone: str = "Asia/Kolkata"
    avg_resolution_seconds: int = 300

class EmailConfigRequest(BaseModel):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    smtp_from: str = ""
    smtp_use_tls: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# User Authentication (email-only, no password)
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.post("/auth")
async def auth_user(req: AuthRequest):
    """Upsert user by email. No password, no tokens — just register/lookup."""
    normalized_email = req.email.strip().lower()
    user_id = await db.upsert_user(normalized_email)
    user = await db.get_user_by_email(normalized_email)
    return {
        "user_id": user_id,
        "email": user["email"],
        "display_name": user.get("display_name", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Caller Entry
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.post("/call")
async def start_call(req: CallRequest):
    """
    New call flow:
    1. Check business hours
    2. Upsert user
    3. Generate room + session + token
    4. Enqueue caller
    5. Create call_log
    """
    # 1. Business hours check
    if business_hours.should_reject_call():
        offline_msg = business_hours.get_offline_tts_message()
        return {
            "rejected": True,
            "offline_message": offline_msg,
        }

    normalized_email = req.email.strip().lower()

    # 2. Upsert user
    user_id = await db.upsert_user(normalized_email)

    # 3. Generate IDs + token
    session_id = str(uuid.uuid4())
    room_id = f"call-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    caller_identity = f"caller-{uuid.uuid4().hex[:8]}"

    token = generate_token(
        room_name=room_id,
        identity=caller_identity,
        name=f"Caller ({normalized_email.split('@')[0][:12]})",
        can_publish=True,
        can_subscribe=True,
    )

    # 5. Create call_log in DB (need position first, so do a quick count)
    from . import queue_engine as qe
    qe._ensure_dept(req.department)
    position = len(qe._department_queues[req.department]) + 1

    call_log_id = await db.create_call_log(
        user_id=user_id,
        session_id=session_id,
        room_id=room_id,
        department=req.department,
        queue_position=position,
    )

    # 4. Enqueue
    queue_info = await queue_engine.enqueue_caller(
        session_id=session_id,
        room_id=room_id,
        caller_id=caller_identity,
        user_email=normalized_email,
        department=req.department,
        user_id=user_id,
        call_log_id=call_log_id,
        skip_outbound=req.skip_outbound,
    )

    return {
        "rejected": False,
        "token": token,
        "url": LIVEKIT_URL,
        "room": room_id,
        "session_id": session_id,
        "caller_identity": caller_identity,
        "queue_position": queue_info["position"],
        "wait_seconds": queue_info["wait_seconds"],
        "wait_message": queue_info["wait_message"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Caller Disconnect
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.delete("/call/{session_id}")
async def disconnect_call(session_id: str):
    """Caller disconnects → triggers abandonment flow."""
    removed = await queue_engine.dequeue_caller(session_id, reason="abandoned")
    if removed:
        return {"removed": True, "session_id": session_id}
    return {"removed": False, "session_id": session_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Queue Info
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.get("/queue")
async def get_queue(department: str = Query("")):
    if department:
        callers = await queue_engine.get_queue_for_department(department)
        return {
            "department": department,
            "queue_depth": len(callers),
            "callers": callers,
            "wait_per_caller_sec": queue_engine._WAIT_PER_CALLER_SEC,
        }
    return await queue_engine.get_all_queues()


@cc_router.get("/queue/all")
async def get_all_queues():
    return await queue_engine.get_all_queues()


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Operations
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.post("/agent/online")
async def agent_go_online(req: AgentOnlineRequest):
    agent = await db.upsert_agent_state(
        agent_identity=req.agent_identity,
        agent_name=req.agent_name,
        department=req.department,
        status="online",
    )
    return {
        "agent_identity": agent["agent_identity"],
        "agent_name": agent["agent_name"],
        "department": agent["department"],
        "sequence_number": agent["sequence_number"],
        "status": agent["status"],
    }


@cc_router.post("/agent/offline")
async def agent_go_offline(req: AgentOfflineRequest):
    await db.set_agent_offline(req.agent_identity)
    return {"status": "offline", "agent_identity": req.agent_identity}


@cc_router.get("/agent/department/{department:path}")
async def get_department_agents(department: str):
    agents = await db.get_agents_in_department(department)
    return {
        "department": department,
        "agents": [
            {
                "agent_identity": a["agent_identity"],
                "agent_name": a["agent_name"],
                "status": a["status"],
                "sequence_number": a["sequence_number"],
                "ignore_outbounds_until": a["ignore_outbounds_until"].isoformat() if a.get("ignore_outbounds_until") else None,
            }
            for a in agents
        ],
    }


@cc_router.post("/agent/accept/{session_id}")
async def agent_accept_call(
    session_id: str,
    agent_identity: str = Query(...),
    agent_name: str = Query("Agent"),
):
    """
    Agent accepts a queued caller:
    1. Pop from queue (cancel TTS)
    2. Update call_log
    3. Set agent busy
    4. Kick piper-tts from room
    5. Generate agent token
    """
    entry = await queue_engine.pop_caller(session_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Session not found in queue")

    room_id = entry["room_id"]
    wait_sec = int(time.time() - entry["joined_at"])

    # Update call_log
    await db.update_call_log_status(
        session_id, "connected",
        wait_seconds=wait_sec,
        agent_id=agent_identity,
    )

    # Set agent busy
    await db.set_agent_busy(agent_identity)

    # Kick piper-tts participants from the room
    try:
        from livekit.api import LiveKitAPI, ListParticipantsRequest
        api_key = os.getenv("LIVEKIT_API_KEY", "devkey")
        api_secret = os.getenv("LIVEKIT_API_SECRET", "devsecret")
        lk_url = LIVEKIT_URL.replace("wss://", "https://")

        async with LiveKitAPI(lk_url, api_key, api_secret) as api:
            participants = await api.room.list_participants(ListParticipantsRequest(room=room_id))
            for p in participants.participants:
                if p.identity.startswith("piper-tts"):
                    try:
                        await api.room.remove_participant(room=room_id, identity=p.identity)
                    except Exception:
                        await api.room.remove_participant(room_id, p.identity)
                    logger.info("Kicked TTS %s from %s", p.identity, room_id)
    except Exception as exc:
        logger.warning("Failed to kick TTS agent: %s", exc)

    # Generate agent token
    token = generate_token(
        room_name=room_id,
        identity=agent_identity,
        name=agent_name,
        can_publish=True,
        can_subscribe=True,
    )

    return {
        "token": token,
        "room": room_id,
        "url": LIVEKIT_URL,
        "session_id": session_id,
        "caller_id": entry["caller_id"],
        "user_email": entry["user_email"],
    }


@cc_router.post("/agent/end-call")
async def agent_end_call(req: AgentEndCallRequest):
    """Agent ends call → update call_log, set agent back online."""
    call_log = await db.get_call_log(req.session_id)
    if call_log:
        created = call_log["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        duration = int((datetime.now(timezone.utc) - created).total_seconds())
        await db.update_call_log_status(
            req.session_id, "completed",
            call_duration=duration,
        )

    await db.set_agent_free(req.agent_identity)
    return {"status": "ended", "agent_identity": req.agent_identity}


# ═══════════════════════════════════════════════════════════════════════════════
# Outbound Callback
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.post("/outbound/accept")
async def outbound_accept(req: OutboundAcceptRequest):
    """
    Agent accepts outbound popup.
    IMPORTANT: do NOT mark completed here; only move to in_progress.
    Finalization happens on /outbound/complete or /outbound/no-answer.
    """
    outbound = await db.get_outbound(req.outbound_id)
    if not outbound:
        raise HTTPException(status_code=404, detail="Outbound callback not found")

    current_status = outbound.get("status")
    if current_status in ("completed", "no_answer", "declined"):
        return {
            "outbound_id": req.outbound_id,
            "status": current_status,
            "already_handled": True,
        }
    if current_status == "in_progress":
        return {
            "outbound_id": req.outbound_id,
            "status": "in_progress",
            "already_handled": True,
        }
    if current_status == "assigned" and outbound.get("assigned_agent") and outbound.get("assigned_agent") != req.agent_identity:
        raise HTTPException(status_code=409, detail="Outbound callback already assigned to another agent")

    await db.mark_outbound_in_progress(req.outbound_id, req.agent_identity)
    logger.info("Outbound %d marked in_progress by %s", req.outbound_id, req.agent_identity)

    room_id = f"outbound-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    token = generate_token(
        room_name=room_id,
        identity=req.agent_identity,
        name="Agent",
        can_publish=True,
        can_subscribe=True,
    )
    await db.set_agent_busy(req.agent_identity)
    logger.info("✓ Agent %s set to BUSY", req.agent_identity)
    
    # Notify caller frontend that agent is ready
    if outbound and outbound.get("user_email"):
        from livekit.websocket import event_hub
        await event_hub.publish({
            "type": "caller_pickup",
            "user_email": outbound["user_email"],
            "room": room_id
        })
        logger.info("✓ WebSocket event published for %s", outbound["user_email"])

    return {
        "token": token,
        "room": room_id,
        "url": LIVEKIT_URL,
        "outbound_id": req.outbound_id,
        "status": "in_progress",
    }


@cc_router.get("/outbound/caller-token")
async def get_caller_token(room: str, user_email: str):
    """Generate a token for the caller to join the outbound callback room."""
    token = generate_token(
        room_name=room,
        identity=user_email,
        name="Caller",
        can_publish=True,
        can_subscribe=True,
    )
    return {
        "token": token,
        "url": LIVEKIT_URL
    }


@cc_router.post("/outbound/complete")
async def outbound_complete(req: OutboundCompleteRequest):
    """Finalize outbound callback as completed after a successful call."""
    current = await db.get_outbound(req.outbound_id)
    if not current:
        raise HTTPException(status_code=404, detail="Outbound callback not found")

    if current.get("status") in ("completed", "no_answer", "declined"):
        await db.set_agent_free(req.agent_identity)
        return {
            "status": current.get("status"),
            "outbound_id": req.outbound_id,
            "already_handled": True,
        }

    await db.complete_outbound(req.outbound_id, "completed")
    await db.set_agent_free(req.agent_identity)
    return {"status": "completed", "outbound_id": req.outbound_id}


@cc_router.post("/outbound/no-answer")
async def outbound_no_answer(req: OutboundNoAnswerRequest):
    """Agent waited in room but remote caller did not join within 25s. End call and send email."""
    # CRITICAL: Check if already completed/finalized  
    current = await db.get_outbound(req.outbound_id)
    if current and current.get("status") in ("completed", "no_answer", "declined"):
        logger.info("Outbound %d already %s, skipping no-answer", req.outbound_id, current.get("status"))
        return {"status": current.get("status"), "outbound_id": req.outbound_id, "already_handled": True}
    
    await db.complete_outbound(req.outbound_id, "no_answer")
    await db.set_agent_free(req.agent_identity)
    
    if req.user_email:
        await send_outbound_no_answer_email(req.user_email, req.department)
        
    return {"status": "no_answer", "outbound_id": req.outbound_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Admin: Clear Queues (Testing)
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.post("/admin/clear-queues")
async def clear_queues():
    """⚠️  DESTRUCTIVE: Clear all old call logs and outbound queue. Use for testing only!"""
    result = await db.clear_queues()
    return {"message": "Cleared outbound_queue, call_logs, offline agents", **result}


@cc_router.post("/outbound/decline")
async def outbound_decline(req: OutboundDeclineRequest):
    """Agent declines → mark as declined (no retry, 1 attempt only), snooze agent."""
    # Don't retry; just mark as declined and move on
    await db.complete_outbound(req.outbound_id, "declined")

    until = datetime.now(timezone.utc) + timedelta(minutes=req.snooze_minutes)
    await db.set_agent_ignoring_outbounds(req.agent_identity, until, req.reason)

    return {
        "status": "declined",
        "snooze_until": until.isoformat(),
    }


@cc_router.post("/outbound/resume")
async def outbound_resume(req: OutboundResumeRequest):
    """Clear ignore_outbounds_until for agent."""
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_states
            SET status = 'online',
                ignore_outbounds_until = NULL,
                ignore_reason = '',
                last_heartbeat = NOW()
            WHERE agent_identity = $1
            """,
            req.agent_identity,
        )
    return {"status": "resumed", "agent_identity": req.agent_identity}


@cc_router.get("/outbound/history")
async def get_outbound_history(department: Optional[str] = None, limit: int = 50):
    """Return outbound callback history, optionally filtered by department."""
    records = await db.get_outbound_history(department=department, limit=limit)
    # Convert datetime objects to ISO strings for JSON serialization
    result = []
    for r in records:
        result.append({
            "id": r["id"],
            "user_email": r["user_email"],
            "department": r["department"],
            "status": r["status"],
            "assigned_agent": r.get("assigned_agent", ""),
            "attempts": r.get("attempts", 0),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "last_attempt": r["last_attempt"].isoformat() if r.get("last_attempt") else None,
        })
    return {"history": result}


# ═══════════════════════════════════════════════════════════════════════════════
# Business Hours / Holiday
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.get("/business-hours")
async def get_business_hours():
    return business_hours.get_status()


@cc_router.post("/holiday")
async def set_holiday(req: HolidayRequest):
    import pytz
    try:
        until_dt = datetime.fromisoformat(req.until)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ISO datetime for 'until'")
    business_hours.set_holiday(req.message, until_dt)
    return {"status": "holiday_set", "message": req.message, "until": req.until}


@cc_router.delete("/holiday")
async def clear_holiday():
    business_hours.clear_holiday()
    return {"status": "holiday_cleared"}


# ═══════════════════════════════════════════════════════════════════════════════
# Admin Config
# ═══════════════════════════════════════════════════════════════════════════════

@cc_router.get("/admin/config")
async def get_admin_config():
    """Get all admin configuration key-value pairs."""
    config = await db.get_all_config()
    return {"config": config}


@cc_router.post("/admin/config")
async def set_admin_config(req: AdminConfigRequest):
    """Set a single admin config value."""
    await db.set_config(req.key, req.value)
    return {"status": "saved", "key": req.key, "value": req.value}


@cc_router.post("/admin/business-hours")
async def set_business_hours(req: BusinessHoursRequest):
    """Update working hours, work days, timezone, and avg resolution time."""
    await db.set_config("work_start", req.work_start)
    await db.set_config("work_end", req.work_end)
    await db.set_config("work_days", req.work_days)
    await db.set_config("timezone", req.timezone)
    await db.set_config("avg_resolution_seconds", str(req.avg_resolution_seconds))
    await business_hours.load_config_from_db()
    return {"status": "updated", **req.model_dump()}


@cc_router.post("/admin/email-config")
async def set_email_config(req: EmailConfigRequest):
    """Save SMTP email settings to DB and reload the email service cache."""
    await db.set_config("smtp_host", req.smtp_host)
    await db.set_config("smtp_port", str(req.smtp_port))
    await db.set_config("smtp_user", req.smtp_user)
    await db.set_config("smtp_password", req.smtp_password)
    await db.set_config("smtp_from", req.smtp_from or req.smtp_user)
    await db.set_config("smtp_use_tls", "true" if req.smtp_use_tls else "false")
    from .email_service import load_email_config_from_db
    await load_email_config_from_db()
    return {"status": "saved", "smtp_user": req.smtp_user, "smtp_host": req.smtp_host}


@cc_router.get("/admin/email-config")
async def get_email_config():
    """Return email config (password masked)."""
    config = await db.get_all_config()
    return {
        "smtp_host":    config.get("smtp_host", "smtp.gmail.com"),
        "smtp_port":    config.get("smtp_port", "587"),
        "smtp_user":    config.get("smtp_user", ""),
        "smtp_from":    config.get("smtp_from", ""),
        "smtp_use_tls": config.get("smtp_use_tls", "true"),
        "smtp_password_set": bool(config.get("smtp_password", "")),
    }
