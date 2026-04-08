# Standalone email scheduler — runs independently of the main call flow.
# Every hour it checks for missed callers (no_answer, 4+ hours old) and
# sends a follow-up email, then marks scheduler_email_sent = TRUE.

import asyncio
import logging

from . import db
from .email_service import send_outbound_no_answer_email

logger = logging.getLogger("callcenter.email_scheduler")

_scheduler_task: asyncio.Task = None
_CHECK_INTERVAL_SEC = 3600  # run every hour


async def start_email_scheduler():
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("Email scheduler started (4-hour missed-caller check every %ds)", _CHECK_INTERVAL_SEC)


async def stop_email_scheduler():
    global _scheduler_task
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
    logger.info("Email scheduler stopped")


async def _scheduler_loop():
    # Wait a bit after startup so DB is ready
    await asyncio.sleep(60)
    while True:
        try:
            await _process_missed_calls()
        except Exception as exc:
            logger.error("Email scheduler error: %s", exc)
        await asyncio.sleep(_CHECK_INTERVAL_SEC)


async def _process_missed_calls():
    missed = await db.get_missed_calls_for_scheduler()
    if not missed:
        logger.debug("Email scheduler: no missed callers to notify")
        return
    logger.info("Email scheduler: sending follow-up to %d missed caller(s)", len(missed))
    for item in missed:
        try:
            await send_outbound_no_answer_email(item["user_email"], item["department"])
            await db.mark_scheduler_email_sent(item["id"])
            logger.info("Scheduler follow-up sent → %s (%s)", item["user_email"], item["department"])
        except Exception as exc:
            logger.error("Failed to send scheduler email to %s: %s", item["user_email"], exc)
