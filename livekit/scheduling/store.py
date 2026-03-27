# [ START ]
#     |
#     v
# +-----------------------------+
# | Lifecycle Methods           |
# | * DB Initialization         |
# +-----------------------------+
#     |
#     | [ open() ]
#     |----> Connect to SQLite (check_same_thread=False)
#     |----> Set row_factory = sqlite3.Row
#     |----> Enable WAL Mode (journal_mode=WAL)
#     |----> Execute schema & index creation script
#     |
#     | [ close() ]
#     |----> Safely close SQLite connection
#     v
# +-----------------------------+
# | Persistence (Write)         |
# +-----------------------------+
#     |
#     | [ upsert(job) ]
#     |----> Serialize job dataclass to JSON string
#     |----> Execute INSERT ... ON CONFLICT(job_id) DO UPDATE
#     |----> Commit transaction
#     |
#     | [ update_status(...) ]
#     |----> Fetch existing job via get()
#     |----> Apply status, error, and timestamps
#     |----> Persist changes via upsert()
#     |
#     | [ delete(job_id) ]
#     |----> Execute DELETE query
#     |----> Return True if rows were affected
#     v
# +-----------------------------+
# | Retrieval (Read)            |
# +-----------------------------+
#     |
#     | [ get(job_id) ]
#     |----> Fetch 'data' JSON blob by ID
#     |----> Deserialize via ScheduledCallJob.from_dict()
#     |
#     | [ list_due(now) ]
#     |----> Query: status='pending' AND scheduled_at <= now
#     |----> Return List of deserialized job objects
#     |
#     | [ list_all(status, ...) ]
#     |----> Query: Filter by status (optional) + LIMIT/OFFSET
#     |----> Order by scheduled_at DESC
#     |
#     | [ count_by_status() ]
#     |----> Query: GROUP BY status
#     |----> Return Map of {status: count}
#     v
# [ YIELD ]

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Optional

from .models import JobStatus, ScheduledCallJob

logger = logging.getLogger("callcenter.scheduling.store")

_DEFAULT_DB_PATH = os.getenv(
    "SCHEDULING_DB_PATH",
    str(Path(__file__).parent.parent.parent / "scheduling_jobs.db"),
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id       TEXT PRIMARY KEY,
    phone_number TEXT NOT NULL,
    scheduled_at REAL NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    data         TEXT NOT NULL,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_status_scheduled
    ON scheduled_jobs (status, scheduled_at);
"""


class JobStore:
    """
    Synchronous SQLite job store.

    All write methods are called from asyncio via run_in_executor for
    non-blocking I/O without requiring aiosqlite as a mandatory dependency.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._path = db_path or _DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        """Open the database and create schema if needed."""
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL mode: readers don't block writers
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_CREATE_TABLE)
        self._conn.commit()
        logger.info("[JobStore] opened db at %s", self._path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def upsert(self, job: ScheduledCallJob) -> None:
        """Insert or replace a job."""
        assert self._conn, "store not opened"
        data = json.dumps(job.to_dict())
        self._conn.execute(
            """
            INSERT INTO scheduled_jobs
                (job_id, phone_number, scheduled_at, status, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                status       = excluded.status,
                data         = excluded.data,
                updated_at   = excluded.updated_at
            """,
            (
                job.job_id, job.phone_number, job.scheduled_at,
                job.status.value, data, job.created_at, job.updated_at,
            ),
        )
        self._conn.commit()

    def get(self, job_id: str) -> Optional[ScheduledCallJob]:
        assert self._conn
        row = self._conn.execute(
            "SELECT data FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row:
            return ScheduledCallJob.from_dict(json.loads(row["data"]))
        return None

    def delete(self, job_id: str) -> bool:
        assert self._conn
        cur = self._conn.execute(
            "DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_due(self, now: Optional[float] = None) -> List[ScheduledCallJob]:
        """Return all pending jobs whose scheduled_at <= now."""
        assert self._conn
        rows = self._conn.execute(
            "SELECT data FROM scheduled_jobs WHERE status = 'pending' AND scheduled_at <= ?",
            (now or time.time(),),
        ).fetchall()
        return [ScheduledCallJob.from_dict(json.loads(r["data"])) for r in rows]

    def list_all(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ScheduledCallJob]:
        assert self._conn
        if status:
            rows = self._conn.execute(
                "SELECT data FROM scheduled_jobs WHERE status = ? "
                "ORDER BY scheduled_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM scheduled_jobs "
                "ORDER BY scheduled_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [ScheduledCallJob.from_dict(json.loads(r["data"])) for r in rows]

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        error: str = "",
        executed_at: Optional[float] = None,
    ) -> None:
        assert self._conn
        job = self.get(job_id)
        if not job:
            return
        job.status     = status
        job.updated_at = time.time()
        job.error      = error
        if executed_at is not None:
            job.executed_at = executed_at
        self.upsert(job)

    def count_by_status(self) -> dict:
        assert self._conn
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM scheduled_jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}
