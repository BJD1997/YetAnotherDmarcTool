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

# Whether the backend answering this query is the one holding the lock. A
# bigint advisory key is stored split across classid (high 32 bits) and objid
# (low 32 bits), with objsubid = 1.
_HOLDS_LOCK_SQL = text(
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted AND objsubid = 1 "
    "AND pid = pg_backend_pid() AND classid::bigint = :hi AND objid::bigint = :lo)"
)

_POOLER_HINT = (
    "leader connection doesn't hold its advisory lock — the connection is likely going through a "
    "transaction-mode pooler (e.g. PgBouncer). Point LEADER_DATABASE_URL at Postgres directly."
)


class LeaderLock:
    """Best-effort leader election via pg_try_advisory_lock. Call try_acquire()
    on an interval; it returns the current leadership state."""

    def __init__(self, lock_key: int) -> None:
        self._lock_key = lock_key
        self._conn: AsyncConnection | None = None

    @property
    def is_leader(self) -> bool:
        return self._conn is not None

    async def _holds_lock(self, conn: AsyncConnection) -> bool:
        key = self._lock_key & 0xFFFFFFFFFFFFFFFF
        held = (await conn.execute(_HOLDS_LOCK_SQL, {"hi": key >> 32, "lo": key & 0xFFFFFFFF})).scalar()
        # Same reasoning as the acquire/release commits below: this query
        # implicitly opens a transaction, so commit it right back — otherwise
        # the connection sits idle-in-transaction for the leader's whole tenure.
        await conn.commit()
        return bool(held)

    async def try_acquire(self) -> bool:
        # Already leader: confirm the held connection is still alive AND still
        # the one holding the lock (a bare SELECT 1 would also "succeed"
        # through a transaction pooler that routed it to some other backend).
        if self._conn is not None:
            try:
                if await self._holds_lock(self._conn):
                    return True
                logger.error(_POOLER_HINT)
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
            # Advisory lock acquisition takes effect immediately server-side,
            # independent of transaction commit — but commit anyway so this
            # connection isn't sitting idle-in-transaction for the leader's
            # whole lifetime.
            await conn.commit()
            if not await self._holds_lock(conn):
                logger.error(_POOLER_HINT)
                await conn.close()
                return False
            self._conn = conn
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
