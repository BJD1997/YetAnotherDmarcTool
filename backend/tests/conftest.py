"""Shared test fixtures.

Most of the suite is pure logic and needs no database. The RLS/tenant-isolation
integration tests (test_rls.py) DO need a real Postgres and are gated on the
TEST_DATABASE_URL env var — an OWNER/superuser async URL, e.g.
    postgresql+asyncpg://postgres:postgres@localhost:5432/dmarc_test
Without it those tests skip, so `pytest` stays fast and Docker-free by default.

CI supplies it via a postgres service container (see .github/workflows/ci.yml).
Locally, spin up a throwaway DB and point the var at it:

    docker run -d --name dmarc-test-db -e POSTGRES_PASSWORD=postgres \\
        -e POSTGRES_DB=dmarc_test -p 55432:5432 postgres:16-alpine
    TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/dmarc_test \\
        pytest tests/test_rls.py

The fixture mirrors the prod two-role split exactly: the OWNER role
(TEST_DATABASE_URL) creates the schema via Alembic and bypasses RLS; a separate
non-owner `dmarc_app` role — the one the api/worker actually connect as, and the
only one FORCE ROW LEVEL SECURITY binds — is what the isolation assertions run
as. (Chosen over testcontainers-python to avoid adding a Python dependency; the
trade-off is that local DB-test runs need a Postgres you start yourself.)
"""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_APP_ROLE = "dmarc_app"
_APP_PASSWORD = "dmarc_app_test"  # test-only; never a real credential
_BACKEND_DIR = Path(__file__).resolve().parents[1]


def _app_url() -> str:
    # render_as_string(hide_password=False), not str(): str(URL) masks the
    # password as "***", which would then be sent literally and fail auth.
    return make_url(TEST_DATABASE_URL).set(username=_APP_ROLE, password=_APP_PASSWORD).render_as_string(
        hide_password=False
    )


async def _prepare_app_role() -> None:
    """Create/refresh the non-owner runtime role, same as db/init/01-create-app-role.sh."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    db_name = make_url(TEST_DATABASE_URL).database
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_APP_ROLE}') THEN
                            CREATE ROLE {_APP_ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
                                PASSWORD '{_APP_PASSWORD}';
                        ELSE
                            ALTER ROLE {_APP_ROLE} WITH PASSWORD '{_APP_PASSWORD}';
                        END IF;
                    END $$;
                    """
                )
            )
            await conn.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {_APP_ROLE}'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Non-owner role + full Alembic schema, once per session. Uses asyncio.run
    for its own setup so it doesn't depend on pytest-asyncio's per-test loop."""
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL not set — skipping database/RLS integration tests")
    asyncio.run(_prepare_app_role())
    # Migrations run as the OWNER (needs CREATE TABLE/POLICY/GRANT), exactly like
    # the prod `migrate` service. env.py reads settings.database_url from DATABASE_URL.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=_BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        check=True,
    )
    return TEST_DATABASE_URL


@pytest_asyncio.fixture
async def rls_sessions(migrated_db):
    """(owner_session, app_session) on fresh tenant data. `owner` is superuser
    (bypasses RLS, for setup); `app` connects as the non-owner dmarc_app role
    that RLS actually binds — the one the running app uses."""
    owner_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    app_engine = create_async_engine(_app_url(), poolclass=NullPool)
    owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with owner_factory() as owner:
        # Clean slate — cascades to every org-scoped table.
        await owner.execute(text("TRUNCATE organizations CASCADE"))
        await owner.commit()
        async with app_factory() as app:
            yield owner, app
    await app_engine.dispose()
    await owner_engine.dispose()
