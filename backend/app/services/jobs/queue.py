"""Postgres-backed work queue for the worker.

Jobs are claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of
worker replicas can drain the queue in parallel without ever double-processing a
job — no Redis/Celery, just the Postgres we already run (see the module docstring
on app/models/background_job.py). The `worker` container runs a consumer loop
over `process_next`; the leader enqueues work on a schedule (app/workers/scheduler.py).
"""

import dataclasses
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.models.background_job import BackgroundJob
from app.models.enums import BackgroundJobStatus

logger = logging.getLogger("worker.queue")

# job_type -> async handler(payload). Handlers must be idempotent / safe to run
# at-least-once (a claimed job whose worker dies mid-run is retried).
JobHandler = Callable[[dict], Awaitable[None]]
_HANDLERS: dict[str, JobHandler] = {}

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_MAX_SECONDS = 3600
# A 'running' job whose worker died is reclaimed after this long (must exceed the
# longest expected handler runtime — a big XML ingest can take a while).
DEFAULT_STALE_SECONDS = 1800

# The partial-unique-index predicate (migration 0023) — used both for ON CONFLICT
# inference on enqueue and kept here as the single source of that expression.
_ACTIVE_PREDICATE = text("status IN ('pending', 'running')")


@dataclasses.dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    job_type: str
    payload: dict
    attempts: int
    max_attempts: int


def register_handler(job_type: str, handler: JobHandler) -> None:
    _HANDLERS[job_type] = handler


async def enqueue(
    db: AsyncSession,
    job_type: str,
    payload: dict | None = None,
    *,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> None:
    """Insert a job and commit. When `dedupe_key` is set and an active
    (pending/running) job already carries it, this is a no-op (ON CONFLICT DO
    NOTHING against the partial unique index) — so the scheduler can enqueue the
    same recurring job every tick without piling up duplicates."""
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
    await db.commit()


async def claim_one(db: AsyncSession, worker_id: str) -> ClaimedJob | None:
    """Atomically claim the next runnable job. `FOR UPDATE SKIP LOCKED` means two
    workers racing here never take the same row — one gets it, the other skips to
    the next. Returns None when there's nothing runnable right now. Increments
    `attempts` on claim, so a job that keeps dying mid-run eventually fails out."""
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
    await db.commit()
    if row is None:
        return None
    return ClaimedJob(
        id=row["id"],
        job_type=row["job_type"],
        payload=row["payload"] or {},
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
    )


async def complete(db: AsyncSession, job_id: uuid.UUID) -> None:
    await db.execute(
        text("UPDATE background_jobs SET status = 'done', last_error = NULL, updated_at = now() WHERE id = :id"),
        {"id": job_id},
    )
    await db.commit()


async def fail(db: AsyncSession, job: ClaimedJob, error: str) -> None:
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
    await db.commit()


async def reclaim_stalled(db: AsyncSession, stale_seconds: int = DEFAULT_STALE_SECONDS) -> int:
    """Return jobs stuck in 'running' past `stale_seconds` (their worker died
    mid-run) back to 'pending' so another worker retries them. Returns the count
    reset. `attempts` was already bumped at claim time, so a repeatedly-crashing
    job still fails out eventually."""
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
    await db.commit()
    return result.rowcount or 0


async def process_next(worker_id: str) -> bool:
    """Claim + run the next job. Returns True if a job was handled (so the caller
    can loop again immediately), False if the queue was idle (caller should
    sleep). Each DB step uses its own short transaction so the (possibly long)
    handler never holds one open."""
    async with async_session_factory() as db:
        job = await claim_one(db, worker_id)
    if job is None:
        return False

    handler = _HANDLERS.get(job.job_type)
    if handler is None:
        async with async_session_factory() as db:
            await fail(db, job, f"no handler registered for job_type={job.job_type!r}")
        logger.error("no handler for job_type=%s (job %s)", job.job_type, job.id)
        return True

    try:
        await handler(job.payload)
    except Exception as exc:  # noqa: BLE001 — any handler error is a job failure, not a worker crash
        logger.exception("job %s (%s) failed", job.id, job.job_type)
        async with async_session_factory() as db:
            await fail(db, job, repr(exc))
        return True

    async with async_session_factory() as db:
        await complete(db, job.id)
    return True
