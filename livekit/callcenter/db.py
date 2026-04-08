# [ START: DATABASE LIFECYCLE ]
#       |
#       v
# +------------------------------------------+
# | init_db()                                |
# | * Lazy-init asyncpg pool (get_pool)      |
# | * Verify/Create: users, call_logs,       |
# |   agent_states, outbound_queue           |
# +------------------------------------------+
#       |
#       | [ CRUD OPERATIONS ]
#       |
#       |--- upsert_user()
#       |    * Register/Update user by email
#       |
#       |--- create_call_log() / update_call_log_status()
#       |    * Track session_id, wait times, and agents
#       |
#       |--- upsert_agent_state()
#       |    * Manage 'online', 'busy', 'offline'
#       |    * Auto-calculate sequence_number
#       |
#       `--- add_to_outbound_queue()
#            * Queue user callbacks with spam prevention
#       |
#       | [ QUEUE MANAGEMENT ]
#       |
#       |--- get_free_agent_for_department()
#       |    * Pick next available agent (Round Robin)
#       |
#       |--- assign_outbound() / complete_outbound()
#       |    * Manage callback lifecycle
#       |
#       `--- get_stuck_outbound() / get_orphaned_outbound()
#            * Identify and reset timed-out assignments
#       |
#       v
# [ CLOSE: close_db() ]

import os
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger("callcenter.db")

_pool: Optional[asyncpg.Pool] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Pool management
# ═══════════════════════════════════════════════════════════════════════════════

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL not set in environment")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        logger.info("asyncpg pool created")
    return _pool


async def init_db():
    """Create the pool and run CREATE TABLE IF NOT EXISTS for all tables."""
    pool = await get_pool()

    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id            SERIAL PRIMARY KEY,
        email         VARCHAR(255) UNIQUE NOT NULL,
        display_name  VARCHAR(100) DEFAULT '',
        created_at    TIMESTAMPTZ DEFAULT NOW(),
        last_seen     TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS call_logs (
        id            SERIAL PRIMARY KEY,
        user_id       INTEGER REFERENCES users(id),
        session_id    VARCHAR(100) UNIQUE NOT NULL,
        room_id       VARCHAR(100) NOT NULL,
        department    VARCHAR(100) DEFAULT '',
        queue_position INTEGER DEFAULT 0,
        status        VARCHAR(30) DEFAULT 'queued',
        wait_seconds  INTEGER DEFAULT 0,
        call_duration INTEGER DEFAULT 0,
        agent_id      VARCHAR(100) DEFAULT '',
        created_at    TIMESTAMPTZ DEFAULT NOW(),
        ended_at      TIMESTAMPTZ
    );

    CREATE TABLE IF NOT EXISTS agent_states (
        id                     SERIAL PRIMARY KEY,
        agent_identity         VARCHAR(100) UNIQUE NOT NULL,
        agent_name             VARCHAR(100) DEFAULT '',
        department             VARCHAR(100) NOT NULL,
        status                 VARCHAR(30) DEFAULT 'offline',
        sequence_number        INTEGER DEFAULT 0,
        ignore_outbounds_until TIMESTAMPTZ,
        ignore_reason          TEXT DEFAULT '',
        went_online_at         TIMESTAMPTZ,
        last_heartbeat         TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS outbound_queue (
        id                    SERIAL PRIMARY KEY,
        call_log_id           INTEGER REFERENCES call_logs(id),
        user_email            VARCHAR(255) NOT NULL,
        department            VARCHAR(100) DEFAULT '',
        status                VARCHAR(30) DEFAULT 'pending',
        assigned_agent        VARCHAR(100) DEFAULT '',
        attempts              INTEGER DEFAULT 0,
        created_at            TIMESTAMPTZ DEFAULT NOW(),
        last_attempt          TIMESTAMPTZ,
        scheduler_email_sent  BOOLEAN DEFAULT FALSE
    );

    CREATE TABLE IF NOT EXISTS admin_config (
        key        VARCHAR(100) PRIMARY KEY,
        value      TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """
    async with pool.acquire() as conn:
        await conn.execute(ddl)
        # ALTER for existing tables that may not have the new column
        await conn.execute(
            "ALTER TABLE outbound_queue ADD COLUMN IF NOT EXISTS scheduler_email_sent BOOLEAN DEFAULT FALSE"
        )
        # Seed default admin config (do not overwrite existing values)
        await conn.execute("""
            INSERT INTO admin_config (key, value) VALUES
                ('avg_resolution_seconds', '300'),
                ('work_start', '09:00'),
                ('work_end', '18:00'),
                ('timezone', 'Asia/Kolkata'),
                ('work_days', '0,1,2,3,4,5'),
                ('smtp_host', 'smtp.gmail.com'),
                ('smtp_port', '587'),
                ('smtp_user', ''),
                ('smtp_password', ''),
                ('smtp_from', ''),
                ('smtp_use_tls', 'true'),
                ('tts_interval_seconds', '10'),
                ('tts_queue_message', 'You are at position {pos} in the queue. Estimated wait: {wait} minutes.')
            ON CONFLICT (key) DO NOTHING
        """)
    logger.info("All call-center tables created / verified")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("asyncpg pool closed")


async def clear_queues():
    """Clear old call_logs and outbound_queue data. Use with caution!"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM outbound_queue")
        await conn.execute("DELETE FROM call_logs")
        await conn.execute("DELETE FROM agent_states WHERE status != 'online'")
        logger.warning("⚠️  Cleared outbound_queue, call_logs, and offline agents")
    return {"cleared": True}


# ═══════════════════════════════════════════════════════════════════════════════
# Users
# ═══════════════════════════════════════════════════════════════════════════════

async def upsert_user(email: str, display_name: str = "") -> int:
    """Insert or update user by email. Returns user_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, display_name, last_seen)
            VALUES ($1, $2, NOW())
            ON CONFLICT (email)
            DO UPDATE SET last_seen = NOW(),
                         display_name = CASE WHEN $2 = '' THEN users.display_name ELSE $2 END
            RETURNING id
            """,
            email, display_name,
        )
        return row["id"]


async def get_user_by_email(email: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email)
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════════
# Call Logs
# ═══════════════════════════════════════════════════════════════════════════════

async def create_call_log(user_id: int, session_id: str, room_id: str,
                          department: str, queue_position: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO call_logs (user_id, session_id, room_id, department, queue_position)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            user_id, session_id, room_id, department, queue_position,
        )
        return row["id"]


async def update_call_log_status(session_id: str, status: str, **kwargs):
    pool = await get_pool()
    sets = ["status = $2"]
    params = [session_id, status]
    idx = 3
    for key in ("wait_seconds", "agent_id", "call_duration"):
        if key in kwargs:
            sets.append(f"{key} = ${idx}")
            params.append(kwargs[key])
            idx += 1
    if "ended_at" in kwargs:
        sets.append(f"ended_at = ${idx}")
        params.append(kwargs["ended_at"])
        idx += 1
    elif status in ("completed", "abandoned"):
        sets.append(f"ended_at = ${idx}")
        params.append(datetime.now(timezone.utc))
        idx += 1

    query = f"UPDATE call_logs SET {', '.join(sets)} WHERE session_id = $1"
    async with pool.acquire() as conn:
        await conn.execute(query, *params)


async def get_call_log(session_id: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM call_logs WHERE session_id = $1", session_id)
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════════
# Agent States
# ═══════════════════════════════════════════════════════════════════════════════

async def upsert_agent_state(agent_identity: str, agent_name: str,
                              department: str, status: str) -> dict:
    """
    Insert or update agent state. When status='online', auto-calculate
    sequence_number as (count of agents already online in dept + 1).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        seq = 0
        if status == "online":
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM agent_states WHERE department = $1 AND status != 'offline'",
                department,
            )
            seq = count + 1

        row = await conn.fetchrow(
            """
            INSERT INTO agent_states
                (agent_identity, agent_name, department, status, sequence_number, went_online_at, last_heartbeat)
            VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
            ON CONFLICT (agent_identity)
            DO UPDATE SET
                agent_name     = $2,
                department     = $3,
                status         = $4,
                sequence_number= $5,
                went_online_at = CASE WHEN $4 = 'online' THEN NOW() ELSE agent_states.went_online_at END,
                last_heartbeat = NOW()
            RETURNING *
            """,
            agent_identity, agent_name, department, status, seq,
        )
        return dict(row)


async def get_agents_in_department(department: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM agent_states WHERE department = $1 ORDER BY sequence_number ASC",
            department,
        )
        return [dict(r) for r in rows]


async def get_all_online_agents() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM agent_states WHERE status != 'offline' ORDER BY department, sequence_number"
        )
        return [dict(r) for r in rows]


async def set_agent_offline(agent_identity: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_states SET status = 'offline', last_heartbeat = NOW() WHERE agent_identity = $1",
            agent_identity,
        )


async def set_agent_busy(agent_identity: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_states SET status = 'busy', last_heartbeat = NOW() WHERE agent_identity = $1",
            agent_identity,
        )


async def set_agent_free(agent_identity: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agent_states SET status = 'online', last_heartbeat = NOW() WHERE agent_identity = $1",
            agent_identity,
        )


async def set_agent_ignoring_outbounds(agent_identity: str, until_ts: datetime, reason: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE agent_states
            SET status = 'ignoring_outbounds',
                ignore_outbounds_until = $2,
                ignore_reason = $3,
                last_heartbeat = NOW()
            WHERE agent_identity = $1
            """,
            agent_identity, until_ts, reason,
        )


async def get_free_agent_for_department(department: str) -> Optional[dict]:
    """
    Returns the agent with the lowest sequence_number that is 'online'
    (not 'busy' or 'ignoring_outbounds') in the given department.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM agent_states
            WHERE department = $1 AND status = 'online'
            ORDER BY sequence_number ASC
            LIMIT 1
            """,
            department,
        )
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════════
# Outbound Queue
# ═══════════════════════════════════════════════════════════════════════════════

async def add_to_outbound_queue(call_log_id: int, user_email: str, department: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Prevent spam: check if user already has an active pending/assigned callback
        existing = await conn.fetchrow(
            """
            SELECT id FROM outbound_queue 
            WHERE user_email = $1 
              AND status IN ('pending', 'assigned', 'in_progress')
              AND (status != 'pending' OR attempts = 0)
            LIMIT 1
            """,
            user_email
        )
        if existing:
            return existing["id"]

        row = await conn.fetchrow(
            """
            INSERT INTO outbound_queue (call_log_id, user_email, department)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            call_log_id, user_email, department,
        )
        return row["id"]


async def get_pending_outbound(department: str = "") -> list[dict]:
    """Get only FRESH pending items (attempts=0). Items with attempts > 0 are handled separately."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if department:
            rows = await conn.fetch(
                "SELECT * FROM outbound_queue WHERE status = 'pending' AND attempts = 0 AND department = $1 ORDER BY created_at ASC",
                department,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM outbound_queue WHERE status = 'pending' AND attempts = 0 ORDER BY created_at ASC"
            )
        return [dict(r) for r in rows]


async def assign_outbound(outbound_id: int, agent_identity: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE outbound_queue
            SET status = 'assigned', assigned_agent = $2, last_attempt = NOW(), attempts = attempts + 1
            WHERE id = $1
            """,
            outbound_id, agent_identity,
        )


async def mark_outbound_in_progress(outbound_id: int, agent_identity: str):
    """
    Mark callback as actively in progress after agent accepts popup.
    Keeps attempts unchanged (one-shot callback policy).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE outbound_queue
            SET status = 'in_progress', assigned_agent = $2, last_attempt = NOW()
            WHERE id = $1
            """,
            outbound_id, agent_identity,
        )


async def complete_outbound(outbound_id: int, status: str):
    """status: 'completed' | 'no_answer' | 'declined': final end-state (single-attempt policy)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status in ("completed", "no_answer", "declined"):
            # Final outcome: stop retrying for this callback record
            await conn.execute(
                "UPDATE outbound_queue SET status = $2, last_attempt = NOW() WHERE id = $1",
                outbound_id, status,
            )


async def get_stuck_outbound(timeout_seconds: int = 45) -> list[dict]:
    """Fetch outbound items that are 'assigned' but last_attempt was > timeout ago."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM outbound_queue
            WHERE status = 'assigned'
              AND last_attempt < NOW() - (INTERVAL '1 second' * $1)
            """,
            timeout_seconds,
        )
        return [dict(r) for r in rows]


async def get_orphaned_outbound(timeout_minutes: int = 60) -> list[dict]:
    """Get pending items with attempts > 0 that haven't been touched in a while (stuck). Exclude completed items."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM outbound_queue
            WHERE status = 'pending'
              AND attempts > 0
              AND (last_attempt IS NULL OR last_attempt < NOW() - (INTERVAL '1 minute' * $1))
            UNION ALL
            SELECT * FROM outbound_queue
            WHERE status = 'assigned'
              AND attempts > 0
              AND (last_attempt IS NULL OR last_attempt < NOW() - (INTERVAL '1 minute' * $1))
            UNION ALL
            SELECT * FROM outbound_queue
            WHERE status = 'in_progress'
              AND attempts > 0
              AND (last_attempt IS NULL OR last_attempt < NOW() - (INTERVAL '1 minute' * $1))
            """,
            timeout_minutes,
        )
        return [dict(r) for r in rows]

async def get_outbound(outbound_id: int) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM outbound_queue WHERE id = $1", outbound_id)
        return dict(row) if row else None


async def reset_outbound_to_pending(outbound_id: int):
    """Reset an outbound item back to pending (used only if explicitly needed)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE outbound_queue SET status = 'pending', assigned_agent = '', last_attempt = NOW() WHERE id = $1",
            outbound_id,
        )


async def count_free_agents(department: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM agent_states WHERE department = $1 AND status = 'online'",
            department,
        )
        return int(count or 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Admin Config
# ═══════════════════════════════════════════════════════════════════════════════

async def get_config(key: str) -> Optional[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM admin_config WHERE key = $1", key)
        return row["value"] if row else None


async def get_all_config() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM admin_config ORDER BY key")
        return {r["key"]: r["value"] for r in rows}


async def set_config(key: str, value: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO admin_config (key, value, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = NOW()
            """,
            key, value,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Email Scheduler
# ═══════════════════════════════════════════════════════════════════════════════

async def get_missed_calls_for_scheduler() -> list[dict]:
    """Get no_answer outbound items older than 4h that haven't had a scheduler email."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, user_email, department FROM outbound_queue
            WHERE status = 'no_answer'
              AND scheduler_email_sent = FALSE
              AND last_attempt < NOW() - INTERVAL '4 hours'
            ORDER BY last_attempt ASC
            """
        )
        return [dict(r) for r in rows]


async def mark_scheduler_email_sent(outbound_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE outbound_queue SET scheduler_email_sent = TRUE WHERE id = $1",
            outbound_id,
        )


async def get_outbound_history(department: str = None, limit: int = 50) -> list[dict]:
    """Fetch recent outbound callbacks, optionally filtered by department."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if department:
            rows = await conn.fetch(
                """
                SELECT id, user_email, department, status, assigned_agent,
                       attempts, created_at, last_attempt
                FROM outbound_queue
                WHERE department = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                department, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, user_email, department, status, assigned_agent,
                       attempts, created_at, last_attempt
                FROM outbound_queue
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]
