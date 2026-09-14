"""Advisory-lock leader election for the worker.

Exactly one worker replica holds a session-level Postgres advisory lock and acts
as the scheduler leader (enqueues due work + runs the stalled-job reaper). If it
dies, its dedicated DB connection drops and Postgres releases the lock
automatically, so another replica acquires it on its next attempt — automatic
failover with no extra coordination service. The lock is held on its own
NullPool connection (not a pooled one) so its lifecycle is exactly the leader's
and it never starves the worker's normal connection pool.

Uses settings.leader_database_url (falling back to settings.database_url) —
see the setting's own docstring in app/config.py for why this must bypass a
transaction-mode connection pooler if one is in front of the app.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger("worker.leader")

# Dedicated engine: each connect() is a real, standalone connection whose close
# (or process death) releases the advisory lock deterministically.
_engine = create_async_engine(settings.leader_database_url or settings.database_url, poolclass=NullPool)


class LeaderLock:
    """Best-effort leader election via pg_try_advisory_lock. Call try_acquire()
    on an interval; it returns the current leadership state."""

    def __init__(self, lock_key: int) -> None:
        self._lock_key = lock_key
        self._conn: AsyncConnection | None = None

    @property
    def is_leader(self) -> bool:
        return self._conn is not None

    async def try_acquire(self) -> bool:
        # Already leader: confirm the held connection is still alive; if it
        # dropped, relinquish so someone (maybe us, next tick) can re-elect.
        if self._conn is not None:
            try:
                await self._conn.execute(text("SELECT 1"))
                # Same reasoning as the acquire/release commits below: this
                # SELECT implicitly opens a transaction, so commit it right
                # back — otherwise every keepalive tick re-opens an
                # uncommitted transaction and the connection sits
                # idle-in-transaction for the leader's whole tenure anyway.
                await self._conn.commit()
                return True
            except Exception:
                logger.warning("leader connection lost — relinquishing leadership")
                await self._drop_connection()

        conn = await _engine.connect()
        try:
            acquired = (
                await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self._lock_key})
            ).scalar()
        except Exception:
            await conn.close()
            raise
        if acquired:
            self._conn = conn
            # Advisory lock acquisition takes effect immediately server-side,
            # independent of transaction commit — but commit anyway so this
            # connection isn't sitting idle-in-transaction for the leader's
            # whole lifetime.
            await conn.commit()
            logger.info("acquired scheduler leadership (advisory lock %s)", self._lock_key)
            return True
        await conn.close()
        return False

    async def _drop_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass

    async def release(self) -> None:
        """Explicitly release (best-effort — process exit would release it anyway)."""
        if self._conn is not None:
            try:
                await self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self._lock_key})
                # Same reasoning as try_acquire(): commit so the connection
                # isn't left idle-in-transaction before we close it.
                await self._conn.commit()
            except Exception:
                pass
        await self._drop_connection()
        logger.info("released scheduler leadership")
