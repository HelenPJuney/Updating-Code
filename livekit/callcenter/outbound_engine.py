# [ START: MONITOR LIFECYCLE ]
#       |
#       |--- start_outbound_monitor()
#       |    * Create asyncio background task
#       v
# +------------------------------------------+
# | BACKGROUND: _monitor_loop()              |
# | * Executes every 5 seconds               |
# +------------------------------------------+
#       |
#       | [ STEP 1: MATCHING ]
#       |----> db.get_pending_outbound()
#       |      * For each item:
#       |        - Find agent via db.get_free_agent_for_department()
#       |        - Check _is_agent_ignoring()
#       |        - If Match: db.assign_outbound() + WebSocket push
#       |
#       | [ STEP 2: TIMEOUT CLEANUP ]
#       |----> db.get_stuck_outbound(25s)
#       |      * For each:
#       |        - Mark 'no_answer'
#       |        - Trigger send_outbound_no_answer_email()
#       |
#       | [ STEP 3: ORPHAN CLEANUP ]
#       |----> db.get_orphaned_outbound(60m)
#       |      * For each:
#       |        - Mark 'no_answer'
#       |        - Trigger send_outbound_no_answer_email()
#       v
# +------------------------------------------+
# | [ Shutdown ]                             |
# | * stop_outbound_monitor()                |
# | * Cancel task + CancelledError handling   |
# +------------------------------------------+
#       |
#       v
# [ END: MONITOR STOPPED ]

import asyncio
import logging
from datetime import datetime, timezone

from . import db
from .email_service import send_outbound_no_answer_email

logger = logging.getLogger("callcenter.outbound")

_outbound_task: asyncio.Task = None


async def start_outbound_monitor():
    """Start the background polling loop. Called from app lifespan."""
    global _outbound_task
    _outbound_task = asyncio.create_task(_monitor_loop())
    logger.info("Outbound monitor started")


async def stop_outbound_monitor():
    """Stop the background loop. Called from app shutdown."""
    global _outbound_task
    if _outbound_task:
        _outbound_task.cancel()
        try:
            await _outbound_task
        except asyncio.CancelledError:
            pass
        _outbound_task = None
    logger.info("Outbound monitor stopped")


def _is_agent_ignoring(agent: dict) -> bool:
    """Check if agent has ignore_outbounds_until set and it's in the future."""
    until = agent.get("ignore_outbounds_until")
    if until:
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < until
    return False


async def _monitor_loop():
    """
    Every 5 seconds:
    1. Fetch FRESH 'pending' items (attempts=0) from outbound_queue
    2. For each, check if a free agent exists in that department
    3. If yes, mark the outbound as 'assigned' and push a WebSocket event
    """
    while True:
        try:
            pending = await db.get_pending_outbound()
            from livekit.websocket import event_hub

            if pending:
                for item in pending:
                    agent = await db.get_free_agent_for_department(item["department"])
                    if agent and not _is_agent_ignoring(agent):
                        await db.assign_outbound(item["id"], agent["agent_identity"])
                        await event_hub.publish({
                            "type": "outbound_callback",
                            "outbound_id": item["id"],
                            "user_email": item["user_email"],
                            "department": item["department"],
                            "countdown": 20,
                            "target_agent": agent["agent_identity"],
                            "attempt_number": 1,
                        })
                        logger.info(
                            "Outbound %d assigned to %s for %s (attempt 1)",
                            item["id"], agent["agent_identity"], item["user_email"],
                        )

            # Cleanup stuck 'assigned' items after 25s timeout (agent didn't accept or join)
            stuck_items = await db.get_stuck_outbound(timeout_seconds=25)
            for item in stuck_items:
                # Check if it's already been completed by agent accept
                current = await db.get_outbound(item["id"])
                if current and current.get("status") in ("completed", "no_answer", "declined"):
                    logger.info("Outbound %d already completed with status=%s, skipping", item["id"], current.get("status"))
                    continue
                    
                await db.complete_outbound(item["id"], "no_answer")
                logger.info(
                    "Outbound %d → no_answer: agent did not accept (25s timeout)",
                    item["id"],
                )
                if item["user_email"]:
                    await send_outbound_no_answer_email(item["user_email"], item["department"])
            
            # Cleanup abandoned items (pending with attempts > 0 for 1+ hour)
            orphaned = await db.get_orphaned_outbound(timeout_minutes=60)
            for item in orphaned:
                # Skip if already finalized
                if item.get("status") in ("completed", "no_answer", "declined"):
                    continue
                await db.complete_outbound(item["id"], "no_answer")
                logger.info(
                    "Outbound %d → no_answer: abandoned after %d attempt(s)",
                    item["id"], item.get("attempts", 0),
                )
                if item["user_email"]:
                    await send_outbound_no_answer_email(item["user_email"], item["department"])

        except Exception as e:
            logger.error("Outbound monitor error: %s", e)
        await asyncio.sleep(5)

