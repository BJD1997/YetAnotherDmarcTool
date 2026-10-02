"""Orchestration for the worker's Postgres work queue: the job-handler
registry and the claim-dispatch-complete loop. The actual queue mechanics
(enqueue, claim, complete, fail, reclaim) live in app/repositories/jobs.py —
this module calls them and owns each short-lived session's commit."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.db.session import async_session_factory
from app.repositories.jobs import (
    ClaimedJob,
    claim_one_job,
    complete_job,
    enqueue_job,
    fail_job,
    prune_finished_jobs as _prune_finished_jobs_query,
    reclaim_stalled_jobs,
)

logger = logging.getLogger("worker.queue")

# job_type -> async handler(payload). Handlers must be idempotent / safe to run
# at-least-once (a claimed job whose worker dies mid-run is retried).
JobHandler = Callable[[dict], Awaitable[None]]
_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str, handler: JobHandler) -> None:
    _HANDLERS[job_type] = handler


async def enqueue(
    db,
    job_type: str,
    payload: dict | None = None,
    *,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> None:
    await enqueue_job(db, job_type, payload, dedupe_key=dedupe_key, run_after=run_after, max_attempts=max_attempts)
    await db.commit()


async def claim_one(db, worker_id: str) -> ClaimedJob | None:
    job = await claim_one_job(db, worker_id)
    await db.commit()
    return job


async def complete(db, job_id) -> None:
    await complete_job(db, job_id)
    await db.commit()


async def fail(db, job: ClaimedJob, error: str) -> None:
    await fail_job(db, job, error)
    await db.commit()


async def reclaim_stalled(db, stale_seconds: int) -> int:
    n = await reclaim_stalled_jobs(db, stale_seconds)
    await db.commit()
    return n


async def prune_finished_jobs(max_age_seconds: int = 604800) -> None:
    """Delete done/failed jobs older than max_age_seconds (default 7 days) —
    run periodically by the `background_jobs_prune` background job."""
    async with async_session_factory() as db:
        await _prune_finished_jobs_query(db, max_age_seconds)
        await db.commit()


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
