"""Integration test for advisory-lock leader election (app/services/jobs/leader.py).

Two LeaderLock instances contend for the same key against a real Postgres; only
one may hold it, and it hands over after release. Gated on TEST_DATABASE_URL.
"""

from sqlalchemy import text
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


async def test_lock_ownership_check_handles_keys_beyond_32_bits(app_db_url, monkeypatch):
    engine = create_async_engine(app_db_url, poolclass=NullPool)
    monkeypatch.setattr(leader, "_engine", engine)
    try:
        for key in (-12345, 0x7FFF_FFFF_0000_0001, -(2**63)):
            lock = leader.LeaderLock(key)
            assert await lock.try_acquire() is True
            assert await lock.try_acquire() is True  # keepalive sees it as still held
            await lock.release()
    finally:
        await engine.dispose()


async def test_leader_notices_when_its_connection_no_longer_holds_the_lock(app_db_url, monkeypatch, caplog):
    """What a transaction-mode pooler looks like from the leader's side: the
    connection still answers queries, but not as the backend holding the lock.
    A bare SELECT 1 keepalive would carry on believing it's leader."""
    engine = create_async_engine(app_db_url, poolclass=NullPool)
    monkeypatch.setattr(leader, "_engine", engine)
    lock = leader.LeaderLock(_TEST_KEY)
    try:
        assert await lock.try_acquire() is True
        stale_conn = lock._conn
        await stale_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _TEST_KEY})
        await stale_conn.commit()

        assert await lock.try_acquire() is True
        assert lock._conn is not stale_conn  # re-elected on a connection that really holds it
        assert "LEADER_DATABASE_URL" in caplog.text
        await lock.release()
    finally:
        await engine.dispose()
