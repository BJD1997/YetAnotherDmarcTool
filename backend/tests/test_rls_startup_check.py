import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db import session as db_session

from tests.conftest import TEST_DATABASE_URL


@pytest_asyncio.fixture
async def engines(migrated_db, app_db_url):
    app_engine = create_async_engine(app_db_url, poolclass=NullPool)
    superuser_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield app_engine, superuser_engine
    await app_engine.dispose()
    await superuser_engine.dispose()


async def test_app_role_passes(engines, monkeypatch):
    app_engine, _ = engines
    monkeypatch.setattr(db_session, "engine", app_engine)
    monkeypatch.setattr(db_session, "_read_engine", app_engine)

    await db_session.assert_rls_enforced()


async def test_superuser_primary_refuses_to_start(engines, monkeypatch):
    app_engine, superuser_engine = engines
    monkeypatch.setattr(db_session, "engine", superuser_engine)
    monkeypatch.setattr(db_session, "_read_engine", None)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        await db_session.assert_rls_enforced()


async def test_superuser_read_replica_refuses_to_start(engines, monkeypatch):
    app_engine, superuser_engine = engines
    monkeypatch.setattr(db_session, "engine", app_engine)
    monkeypatch.setattr(db_session, "_read_engine", superuser_engine)

    with pytest.raises(RuntimeError, match="DATABASE_READ_URL"):
        await db_session.assert_rls_enforced()
