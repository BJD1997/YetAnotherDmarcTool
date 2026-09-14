"""Integration test for advisory-lock leader election (app/services/jobs/leader.py).

Two LeaderLock instances contend for the same key against a real Postgres; only
one may hold it, and it hands over after release. Gated on TEST_DATABASE_URL.
"""

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.services.jobs import leader

_TEST_KEY = 0x7E57  # isolated from the app's real leader_lock_key


async def test_only_one_leader_at_a_time(app_db_url, monkeypatch):
    engine = create_async_engine(app_db_url, poolclass=NullPool)
    monkeypatch.setattr(leader, "_engine", engine)
    a = leader.LeaderLock(_TEST_KEY)
    b = leader.LeaderLock(_TEST_KEY)
    try:
        assert await a.try_acquire() is True
        assert a.is_leader is True

        # b cannot take a lock a already holds.
        assert await b.try_acquire() is False
        assert b.is_leader is False

        # a re-confirming leadership is idempotent.
        assert await a.try_acquire() is True

        # After a releases, b can take over.
        await a.release()
        assert a.is_leader is False
        assert await b.try_acquire() is True
        assert b.is_leader is True
        await b.release()
    finally:
        await engine.dispose()
