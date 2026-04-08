from datetime import datetime, time as dtime
import pytz
import os
import logging

logger = logging.getLogger("callcenter.business_hours")

# ── In-memory config cache (refreshed from DB on startup / admin update) ──────
_config = {
    "work_start": dtime(9, 0),
    "work_end":   dtime(18, 0),
    "timezone":   "Asia/Kolkata",
    "work_days":  [0, 1, 2, 3, 4, 5],   # Mon–Sat (Python weekday: Mon=0)
}

# ── In-memory holiday state ───────────────────────────────────────────────────
_holiday_active  = False
_holiday_message = ""
_holiday_until   = None  # datetime — holiday expires at this UTC time


# ═══════════════════════════════════════════════════════════════════════════════
# DB config loader (call once on startup, and after admin saves settings)
# ═══════════════════════════════════════════════════════════════════════════════

async def load_config_from_db():
    """Load working-hours config from admin_config table into in-memory cache."""
    global _config
    try:
        from . import db
        work_start_str = await db.get_config("work_start") or "09:00"
        work_end_str   = await db.get_config("work_end")   or "18:00"
        tz             = await db.get_config("timezone")   or "Asia/Kolkata"
        work_days_str  = await db.get_config("work_days")  or "0,1,2,3,4,5"

        sh, sm = map(int, work_start_str.split(":"))
        eh, em = map(int, work_end_str.split(":"))
        days   = [int(d) for d in work_days_str.split(",") if d.strip().isdigit()]

        _config = {
            "work_start": dtime(sh, sm),
            "work_end":   dtime(eh, em),
            "timezone":   tz,
            "work_days":  days,
        }
        logger.info("Business-hours config loaded: %s–%s %s days=%s",
                    work_start_str, work_end_str, tz, work_days_str)
    except Exception as exc:
        logger.warning("Could not load business-hours config from DB: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# Core checks
# ═══════════════════════════════════════════════════════════════════════════════

def is_within_business_hours() -> bool:
    if os.getenv("IGNORE_BUSINESS_HOURS", "false").lower() in ("true", "1", "yes"):
        return True
    now = datetime.now(pytz.timezone(_config["timezone"]))
    return (
        _config["work_start"] <= now.time() <= _config["work_end"]
        and now.weekday() in _config["work_days"]
    )


def is_holiday_mode() -> bool:
    global _holiday_active, _holiday_until, _holiday_message
    if _holiday_until and datetime.now(pytz.timezone(_config["timezone"])) > _holiday_until:
        _holiday_active  = False
        _holiday_until   = None
        _holiday_message = ""
    return _holiday_active


def set_holiday(message: str, until: datetime):
    global _holiday_active, _holiday_message, _holiday_until
    _holiday_active  = True
    _holiday_message = message
    _holiday_until   = until


def clear_holiday():
    global _holiday_active, _holiday_message, _holiday_until
    _holiday_active  = False
    _holiday_message = ""
    _holiday_until   = None


def get_offline_tts_message() -> str:
    if is_holiday_mode():
        return _holiday_message or "We are currently closed for a holiday. Please call back on the next working day."
    start_str = _config["work_start"].strftime("%-I %p").lstrip("0") if hasattr(dtime, 'strftime') else "9 AM"
    end_str   = _config["work_end"].strftime("%-I %p").lstrip("0")   if hasattr(dtime, 'strftime') else "6 PM"
    return (
        f"Thank you for calling. We are available between {_config['work_start'].strftime('%I:%M %p').lstrip('0')} "
        f"and {_config['work_end'].strftime('%I:%M %p').lstrip('0')}. "
        "Please call back during our business hours. We look forward to assisting you."
    )


def should_reject_call() -> bool:
    return not is_within_business_hours() or is_holiday_mode()


def get_status() -> dict:
    now = datetime.now(pytz.timezone(_config["timezone"]))
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "is_open":        is_within_business_hours() and not is_holiday_mode(),
        "current_time":   now.isoformat(),
        "work_start":     _config["work_start"].strftime("%H:%M"),
        "work_end":       _config["work_end"].strftime("%H:%M"),
        "timezone":       _config["timezone"],
        "work_days":      _config["work_days"],
        "work_days_names":[day_names[d] for d in _config["work_days"]],
        "is_holiday":     is_holiday_mode(),
        "holiday_message":_holiday_message if _holiday_active else "",
        "holiday_until":  _holiday_until.isoformat() if _holiday_until else None,
    }
