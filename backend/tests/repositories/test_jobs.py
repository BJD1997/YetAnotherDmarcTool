import uuid

import pytest
from sqlalchemy import text

from app.repositories.jobs import (
    claim_one_job,
    complete_job,
    enqueue_job,
    fail_job,
    prune_finished_jobs,
    reclaim_stalled_jobs,
)


async def _truncate_background_jobs(owner_factory):
    """Helper to truncate background_jobs table."""
    async with owner_factory() as db:
        await db.execute(text("TRUNCATE background_jobs CASCADE"))
        await db.commit()


async def test_enqueue_and_claim_one_job(api):
    _client, owner_factory = api
    await _truncate_background_jobs(owner_factory)
    async with owner_factory() as db:
        await enqueue_job(db, "noop", {"x": 1})
        await db.commit()

    async with owner_factory() as db:
        job = await claim_one_job(db, "w1")
        await db.commit()
    assert job is not None
    assert job.job_type == "noop"
    assert job.payload == {"x": 1}
    assert job.attempts == 1


async def test_claim_one_job_returns_none_when_empty(api):
    _client, owner_factory = api
    await _truncate_background_jobs(owner_factory)
    async with owner_factory() as db:
        assert await claim_one_job(db, "w1") is None


async def test_dedupe_key_blocks_duplicate_active_jobs(api):
    _client, owner_factory = api
    await _truncate_background_jobs(owner_factory)
    async with owner_factory() as db:
        key = f"sweep:{uuid.uuid4()}"
        await enqueue_job(db, "sweep", dedupe_key=key)
        await enqueue_job(db, "sweep", dedupe_key=key)  # no-op — key already active
        await db.commit()
        job = await claim_one_job(db, "w1")
        await db.commit()
    assert job is not None
    async with owner_factory() as db:
        assert await claim_one_job(db, "w1") is None  # nothing else pending for this key


async def test_complete_job_marks_done(api):
    _client, owner_factory = api
    await _truncate_background_jobs(owner_factory)
    async with owner_factory() as db:
        await enqueue_job(db, "noop")
        await db.commit()
        job = await claim_one_job(db, "w1")
        await complete_job(db, job.id)
        await db.commit()
        # Re-claiming finds nothing — the job is done, not pending.
        assert await claim_one_job(db, "w1") is None


async def test_fail_job_retries_then_parks_as_failed(api):
    _client, owner_factory = api
    await _truncate_background_jobs(owner_factory)
    async with owner_factory() as db:
        await enqueue_job(db, "noop", max_attempts=1)
        await db.commit()
        job = await claim_one_job(db, "w1")
        assert job.attempts == 1
        await fail_job(db, job, "boom")
        await db.commit()
        # max_attempts=1 reached on the first attempt — parked as failed, not retried.
        assert await claim_one_job(db, "w1") is None


async def test_reclaim_stalled_jobs_returns_running_to_pending(api):
    _client, owner_factory = api
    await _truncate_background_jobs(owner_factory)
    async with owner_factory() as db:
        await enqueue_job(db, "noop")
        await db.commit()
        job = await claim_one_job(db, "w1")
        await db.commit()
        # Simulate a crashed worker: backdate the lock well past the threshold.
        await db.execute(
            text("UPDATE background_jobs SET locked_at = now() - make_interval(secs => 3600) WHERE id = :id"),
            {"id": job.id},
        )
        await db.commit()
        assert await reclaim_stalled_jobs(db, stale_seconds=1800) == 1
        await db.commit()
        reclaimed = await claim_one_job(db, "w2")
        await db.commit()
    assert reclaimed is not None
    assert reclaimed.id == job.id


async def test_prune_finished_jobs_removes_old_done_and_failed_leaves_others(api):
    _client, owner_factory = api
    await _truncate_background_jobs(owner_factory)
    async with owner_factory() as db:
        # Old, done — should be pruned.
        await enqueue_job(db, "noop", dedupe_key="old-done")
        await db.commit()
        old_done = await claim_one_job(db, "w1")
        await complete_job(db, old_done.id)
        await db.commit()

        # Old, failed (parked after exhausting attempts) — should be pruned.
        await enqueue_job(db, "noop", dedupe_key="old-failed", max_attempts=1)
        await db.commit()
        old_failed = await claim_one_job(db, "w1")
        await fail_job(db, old_failed, "boom")
        await db.commit()

        # Recent, done — old enough to be finished but not old enough to prune.
        await enqueue_job(db, "noop", dedupe_key="recent-done")
        await db.commit()
        recent_done = await claim_one_job(db, "w1")
        await complete_job(db, recent_done.id)
        await db.commit()

        # Still pending — must never be pruned regardless of age.
        await enqueue_job(db, "noop", dedupe_key="pending")
        await db.commit()

        # Backdate updated_at on the two "old" rows only.
        for old_job_id in (old_done.id, old_failed.id):
            await db.execute(
                text("UPDATE background_jobs SET updated_at = now() - make_interval(secs => 7200) WHERE id = :id"),
                {"id": old_job_id},
            )
        await db.commit()

        pruned = await prune_finished_jobs(db, max_age_seconds=3600)
        await db.commit()
        assert pruned == 2

        remaining = (await db.execute(text("SELECT dedupe_key FROM background_jobs ORDER BY dedupe_key"))).scalars().all()
    assert remaining == ["pending", "recent-done"]
