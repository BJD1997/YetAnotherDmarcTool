"""Integration tests for the Postgres work queue (app/services/jobs/queue.py).

Run as the non-owner dmarc_app role against a real Postgres — proving the
concurrency behavior the worker actually relies on (FOR UPDATE SKIP LOCKED,
dedup, retry/backoff, stalled-job reclaim). Gated on TEST_DATABASE_URL.
"""

import asyncio

from sqlalchemy import text

from app.services.jobs import queue


async def _count(db, sql: str, **params) -> int:
    return (await db.execute(text(sql), params)).scalar_one()


async def test_claim_skip_locked_never_double_claims(app_sessionmaker):
    async with app_sessionmaker() as db:
        for i in range(5):
            await queue.enqueue(db, "noop", {"i": i})

    async def claim(worker_id: str):
        async with app_sessionmaker() as db:
            return await queue.claim_one(db, worker_id)

    # Five workers race for five jobs at once — each must get a distinct one.
    claimed = await asyncio.gather(*[claim(f"w{i}") for i in range(5)])
    ids = [c.id for c in claimed if c is not None]
    assert len(ids) == 5
    assert len(set(ids)) == 5  # no job claimed by two workers

    # Nothing left to claim.
    async with app_sessionmaker() as db:
        assert await queue.claim_one(db, "w") is None


async def test_dedupe_key_blocks_duplicate_active_jobs(app_sessionmaker):
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "sweep", dedupe_key="sweep")
        await queue.enqueue(db, "sweep", dedupe_key="sweep")  # no-op
        assert await _count(db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep'") == 1

    # A running job still counts as active — re-enqueue stays a no-op.
    async with app_sessionmaker() as db:
        job = await queue.claim_one(db, "w")
        await queue.enqueue(db, "sweep", dedupe_key="sweep")
        assert await _count(db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep'") == 1

    # Once it's done, the dedupe slot frees up and a fresh one can be enqueued.
    async with app_sessionmaker() as db:
        await queue.complete(db, job.id)
        await queue.enqueue(db, "sweep", dedupe_key="sweep")
        assert await _count(db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep'") == 2
        assert await _count(
            db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep' AND status = 'pending'"
        ) == 1


async def test_fail_retries_with_backoff_then_fails_out(app_sessionmaker):
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "noop", max_attempts=2)

    async with app_sessionmaker() as db:
        job = await queue.claim_one(db, "w")
        assert job.attempts == 1
        await queue.fail(db, job, "boom")
        row = (
            await db.execute(text("SELECT status, run_after > now() AS deferred FROM background_jobs WHERE id = :id"), {"id": job.id})
        ).mappings().one()
        assert row["status"] == "pending"
        assert row["deferred"] is True  # backoff pushed run_after into the future

    # Backoff means it isn't immediately claimable.
    async with app_sessionmaker() as db:
        assert await queue.claim_one(db, "w") is None

    # Clear the backoff, re-claim (attempts=2 == max), fail -> parked as failed.
    async with app_sessionmaker() as db:
        await db.execute(text("UPDATE background_jobs SET run_after = now() WHERE id = :id"), {"id": job.id})
        await db.commit()
        job2 = await queue.claim_one(db, "w")
        assert job2.attempts == 2
        await queue.fail(db, job2, "boom again")
        status = (await db.execute(text("SELECT status FROM background_jobs WHERE id = :id"), {"id": job.id})).scalar()
        assert status == "failed"


async def test_reclaim_stalled_returns_running_jobs_to_pending(app_sessionmaker):
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "noop")
        job = await queue.claim_one(db, "w")  # -> running
        # Simulate a crashed worker: backdate the lock well past the threshold.
        await db.execute(
            text("UPDATE background_jobs SET locked_at = now() - make_interval(secs => 3600) WHERE id = :id"),
            {"id": job.id},
        )
        await db.commit()
        assert await queue.reclaim_stalled(db, stale_seconds=1800) == 1
        status = (await db.execute(text("SELECT status FROM background_jobs WHERE id = :id"), {"id": job.id})).scalar()
        assert status == "pending"

    # A fresh (recently-locked) running job is NOT reclaimed.
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "noop")
        await queue.claim_one(db, "w")
        assert await queue.reclaim_stalled(db, stale_seconds=1800) == 0


async def test_process_next_runs_handler_and_completes(app_sessionmaker, monkeypatch):
    seen = []

    async def handler(payload: dict) -> None:
        seen.append(payload)

    queue.register_handler("test_ok", handler)
    monkeypatch.setattr(queue, "async_session_factory", app_sessionmaker)

    async with app_sessionmaker() as db:
        await queue.enqueue(db, "test_ok", {"x": 1})

    assert await queue.process_next("w") is True
    assert seen == [{"x": 1}]
    async with app_sessionmaker() as db:
        status = (await db.execute(text("SELECT status FROM background_jobs WHERE job_type = 'test_ok'"))).scalar()
    assert status == "done"

    # Idle queue -> False (caller should sleep).
    assert await queue.process_next("w") is False


async def test_process_next_failing_handler_is_recorded(app_sessionmaker, monkeypatch):
    async def bad(_payload: dict) -> None:
        raise RuntimeError("nope")

    queue.register_handler("test_bad", bad)
    monkeypatch.setattr(queue, "async_session_factory", app_sessionmaker)

    async with app_sessionmaker() as db:
        await queue.enqueue(db, "test_bad", max_attempts=1)

    assert await queue.process_next("w") is True
    async with app_sessionmaker() as db:
        row = (
            await db.execute(text("SELECT status, last_error FROM background_jobs WHERE job_type = 'test_bad'"))
        ).mappings().one()
    assert row["status"] == "failed"  # max_attempts=1 -> first failure parks it
    assert "nope" in row["last_error"]
