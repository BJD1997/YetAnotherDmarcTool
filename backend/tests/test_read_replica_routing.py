"""get_read_db falls back to the primary engine when DATABASE_READ_URL is
unset (the homelab default), and routes to a distinct engine when it is set.
Gated on TEST_DATABASE_URL — uses a second connection to the same test
Postgres to stand in for "a replica" (proving the routing logic itself,
not real streaming replication, which isn't practical to exercise in CI).

Uses the monkeypatch-a-second-sessionmaker approach rather than
importlib.reload(session_module), per the brief's explicitly-sanctioned
alternative. Tried reload first; two concrete problems ruled it out:

1. Half the app imports `engine`/`async_session_factory` BY VALUE straight
   from this module (app/workers/scheduler.py, app/workers/jobs/*, app/
   services/jobs/queue.py, app/services/auth/rate_limit.py, app/services/
   dns_checks/*, app/services/retention/forensic_purge.py, app/middleware/
   demo_read_only.py, ...). reload() rebinds session_module's own attributes
   to freshly-constructed engine/sessionmaker objects, but every one of
   those other modules keeps holding its own reference to the PRE-reload
   objects — so reload silently orphans the old engine's connection pool
   (never disposed) rather than actually replacing it anywhere but this
   module, exactly the "could leak connections" risk the brief flagged.
2. In this sandbox (and in CI — .github/workflows/ci.yml sets only
   TEST_DATABASE_URL, never DATABASE_URL), settings.database_url keeps its
   default `db:5432`, which nothing resolves outside the real docker-compose
   network. That's fine for the rest of the suite (every other DB-touching
   test builds its own engine off TEST_DATABASE_URL/app_db_url rather than
   exercising session_module.engine directly), but it means even the
   "falls back to primary" case here has no reachable primary to fall back
   to without a monkeypatch of its own — so both tests need the same
   technique for consistency, and the "configured replica" test already
   needs monkeypatching to reliably stand up a second real connection.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def test_get_read_db_falls_back_to_primary_when_unset(migrated_db, monkeypatch, app_db_url):
    from app.db import session as session_module

    assert session_module._read_engine is None

    # Stand in for "a reachable primary" — session_module.async_session_factory
    # itself points at settings.database_url's unreachable default in this
    # environment (see module docstring), so route the fallback through a real
    # working sessionmaker instead, same as the app.dependency_overrides swap
    # tests/conftest.py's `api` fixture does for the same underlying reason.
    engine = create_async_engine(app_db_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(session_module, "_read_session_factory", factory)

        async for db in session_module.get_read_db():
            result = await db.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
            break
    finally:
        await engine.dispose()


async def test_get_read_db_uses_configured_replica_engine(migrated_db, monkeypatch, app_db_url):
    from app.db import session as session_module

    engine = create_async_engine(app_db_url, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(session_module, "_read_engine", engine)
        monkeypatch.setattr(session_module, "_read_session_factory", factory)

        assert session_module._read_engine is not None
        async for db in session_module.get_read_db():
            result = await db.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
            break
    finally:
        await engine.dispose()
