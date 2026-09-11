"""Queries backing the worker's Postgres work queue (background_jobs). See
app/services/jobs/queue.py for the orchestration (handler registry, the
claim-dispatch-complete loop) that calls these — none of the functions here
commit; the caller owns each short-lived session's transaction."""

import dataclasses
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job import BackgroundJob
from app.models.enums import BackgroundJobStatus

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_MAX_SECONDS = 3600
# The partial-unique-index predicate (migration 0023) — used both for ON
# CONFLICT inference on enqueue and kept here as the single source of that
# expression.
_ACTIVE_PREDICATE = text("status IN ('pending', 'running')")


@dataclasses.dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    job_type: str
    payload: dict
    attempts: int
    max_attempts: int


async def enqueue_job(
    db: AsyncSession,
    job_type: str,
    payload: dict | None = None,
    *,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> None:
    """Insert a job. When `dedupe_key` is set and an active (pending/running)
    job already carries it, this is a no-op (ON CONFLICT DO NOTHING against
    the partial unique index) — so the leader can enqueue the same recurring
    job every tick without piling up duplicates."""
    stmt = pg_insert(BackgroundJob).values(
        id=uuid.uuid4(),
        job_type=job_type,
        dedupe_key=dedupe_key,
        payload=payload or {},
        status=BackgroundJobStatus.pending.value,
        run_after=run_after or datetime.now(timezone.utc),
        max_attempts=max_attempts,
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"], index_where=_ACTIVE_PREDICATE)
    await db.execute(stmt)


async def claim_one_job(db: AsyncSession, worker_id: str) -> ClaimedJob | None:
    """Atomically claim the next runnable job. `FOR UPDATE SKIP LOCKED` means
    two workers racing here never take the same row — one gets it, the other
    skips to the next. Returns None when there's nothing runnable right now.
    Increments `attempts` on claim, so a job that keeps dying mid-run
    eventually fails out."""
    row = (
        await db.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'running', locked_by = :wid, locked_at = now(),
                    attempts = attempts + 1, updated_at = now()
                WHERE id = (
                    SELECT id FROM background_jobs
                    WHERE status = 'pending' AND run_after <= now()
                    ORDER BY run_after
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, job_type, payload, attempts, max_attempts
                """
            ),
            {"wid": worker_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return ClaimedJob(
        id=row["id"],
        job_type=row["job_type"],
        payload=row["payload"] or {},
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
    )


async def complete_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    await db.execute(
        text("UPDATE background_jobs SET status = 'done', last_error = NULL, updated_at = now() WHERE id = :id"),
        {"id": job_id},
    )


async def fail_job(db: AsyncSession, job: ClaimedJob, error: str) -> None:
    """Retry with exponential backoff until max_attempts, then park as failed."""
    if job.attempts >= job.max_attempts:
        await db.execute(
            text("UPDATE background_jobs SET status = 'failed', last_error = :e, updated_at = now() WHERE id = :id"),
            {"e": error[:2000], "id": job.id},
        )
    else:
        backoff = min(_BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1)), _BACKOFF_MAX_SECONDS)
        await db.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'pending', last_error = :e, locked_by = NULL, locked_at = NULL,
                    run_after = now() + make_interval(secs => :secs), updated_at = now()
                WHERE id = :id
                """
            ),
            {"e": error[:2000], "secs": backoff, "id": job.id},
        )


async def reclaim_stalled_jobs(db: AsyncSession, stale_seconds: int) -> int:
    """Return jobs stuck in 'running' past `stale_seconds` (their worker died
    mid-run) back to 'pending' so another worker retries them. Returns the
    count reset. `attempts` was already bumped at claim time, so a
    repeatedly-crashing job still fails out eventually."""
    result = await db.execute(
        text(
            """
            UPDATE background_jobs
            SET status = 'pending', locked_by = NULL, locked_at = NULL, updated_at = now()
            WHERE status = 'running' AND locked_at < now() - make_interval(secs => :secs)
            """
        ),
        {"secs": stale_seconds},
    )
    return result.rowcount or 0
