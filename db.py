"""SQLite persistence layer for scan jobs and results."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import aiosqlite

from config import settings

_db_path: str = ""


async def init_db() -> None:
    """Create tables if they don't exist. Call once at startup."""
    global _db_path
    _db_path = settings.db_path
    os.makedirs(os.path.dirname(_db_path) or ".", exist_ok=True)

    async with aiosqlite.connect(_db_path) as conn:
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                total_hotels INTEGER NOT NULL,
                scanned_count INTEGER NOT NULL DEFAULT 0,
                duetto_pixel_count INTEGER NOT NULL DEFAULT 0,
                gamechanger_count INTEGER NOT NULL DEFAULT 0,
                competitor_rms_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS job_hotels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                hotel_index INTEGER NOT NULL,
                hotel_name TEXT NOT NULL,
                hotel_website TEXT NOT NULL,
                hotel_city TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_job_hotels_job_id
                ON job_hotels(job_id);
        """)


async def _recover_orphaned_jobs() -> None:
    """Mark jobs stuck in 'running' as 'failed' (e.g. after server restart)."""
    async with aiosqlite.connect(_db_path) as conn:
        now = _now()
        await conn.execute(
            "UPDATE jobs SET status = 'failed', error_message = 'Interrupted by server restart', updated_at = ? WHERE status = 'running'",
            (now,),
        )
        await conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_job(job_id: str, hotels: list[dict]) -> None:
    """Insert a new job and its hotel rows."""
    now = _now()
    async with aiosqlite.connect(_db_path) as conn:
        await conn.execute(
            "INSERT INTO jobs (id, status, total_hotels, created_at, updated_at) VALUES (?, 'pending', ?, ?, ?)",
            (job_id, len(hotels), now, now),
        )
        for i, h in enumerate(hotels):
            await conn.execute(
                "INSERT INTO job_hotels (job_id, hotel_index, hotel_name, hotel_website, hotel_city, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (job_id, i, h["name"], h["website"], h.get("city", ""), now, now),
            )
        await conn.commit()


async def get_job(job_id: str) -> dict | None:
    """Fetch a single job row as a dict."""
    async with aiosqlite.connect(_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_job_hotels(job_id: str) -> list[dict]:
    """Fetch all hotel rows for a job, ordered by index."""
    async with aiosqlite.connect(_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM job_hotels WHERE job_id = ? ORDER BY hotel_index",
            (job_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def update_hotel_status(job_id: str, hotel_index: int, status: str) -> None:
    """Set a hotel's status (e.g. 'scanning')."""
    async with aiosqlite.connect(_db_path) as conn:
        await conn.execute(
            "UPDATE job_hotels SET status = ?, updated_at = ? WHERE job_id = ? AND hotel_index = ?",
            (status, _now(), job_id, hotel_index),
        )
        await conn.commit()


async def save_hotel_result(
    job_id: str, hotel_index: int, result_json: str, is_duetto: bool, is_gc: bool, has_competitor: bool
) -> None:
    """Store a hotel result and update job counters."""
    now = _now()
    async with aiosqlite.connect(_db_path) as conn:
        await conn.execute(
            "UPDATE job_hotels SET status = 'done', result_json = ?, updated_at = ? WHERE job_id = ? AND hotel_index = ?",
            (result_json, now, job_id, hotel_index),
        )
        await conn.execute(
            """UPDATE jobs SET
                scanned_count = scanned_count + 1,
                duetto_pixel_count = duetto_pixel_count + ?,
                gamechanger_count = gamechanger_count + ?,
                competitor_rms_count = competitor_rms_count + ?,
                updated_at = ?
            WHERE id = ?""",
            (int(is_duetto), int(is_gc), int(has_competitor), now, job_id),
        )
        await conn.commit()


async def save_hotel_error(job_id: str, hotel_index: int, error_json: str) -> None:
    """Mark a hotel as errored with its result JSON."""
    now = _now()
    async with aiosqlite.connect(_db_path) as conn:
        await conn.execute(
            "UPDATE job_hotels SET status = 'error', result_json = ?, updated_at = ? WHERE job_id = ? AND hotel_index = ?",
            (error_json, now, job_id, hotel_index),
        )
        await conn.execute(
            "UPDATE jobs SET scanned_count = scanned_count + 1, updated_at = ? WHERE id = ?",
            (now, job_id),
        )
        await conn.commit()


async def mark_job_running(job_id: str) -> None:
    async with aiosqlite.connect(_db_path) as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        await conn.commit()


async def mark_job_done(job_id: str) -> None:
    async with aiosqlite.connect(_db_path) as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        await conn.commit()


async def mark_job_failed(job_id: str, error: str) -> None:
    async with aiosqlite.connect(_db_path) as conn:
        await conn.execute(
            "UPDATE jobs SET status = 'failed', error_message = ?, updated_at = ? WHERE id = ?",
            (error, _now(), job_id),
        )
        await conn.commit()


async def list_jobs(limit: int = 50) -> list[dict]:
    """List all jobs, most recent first."""
    async with aiosqlite.connect(_db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_job_results_json(job_id: str) -> list[str]:
    """Return raw result_json strings for all completed hotels in a job."""
    async with aiosqlite.connect(_db_path) as conn:
        cursor = await conn.execute(
            "SELECT result_json FROM job_hotels WHERE job_id = ? AND result_json IS NOT NULL ORDER BY hotel_index",
            (job_id,),
        )
        return [row[0] for row in await cursor.fetchall()]
