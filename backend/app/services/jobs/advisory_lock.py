from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.db import session as db_session


@asynccontextmanager
async def try_advisory_lock(key: int) -> AsyncIterator[bool]:
    """Yields whether a session-level Postgres advisory lock on `key` was
    acquired, releasing it on exit. Held on its own connection so the
    caller's ORM commits can't hand it to a different pooled connection."""
    async with db_session.engine.connect() as conn:
        acquired = (await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})).scalar_one()
        await conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                await conn.commit()
