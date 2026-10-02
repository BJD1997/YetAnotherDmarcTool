"""ensure_app_role on a server whose admin is not a superuser — Azure
Database for PostgreSQL. SET ROLE to a CREATEROLE-only admin reproduces it:
there, naming the SUPERUSER attribute in ALTER ROLE is refused outright."""

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.scripts import ensure_app_role
from app.scripts.ensure_app_role import ensure_role, scram_verifier

from tests.conftest import TEST_DATABASE_URL

_ADMIN = "yadt_test_azure_admin"
_ROLE = "yadt_test_app_role"


def test_scram_verifier_matches_postgres_format():
    v = scram_verifier("secret", salt=b"0123456789abcdef")
    assert v == scram_verifier("secret", salt=b"0123456789abcdef")
    assert v.startswith("SCRAM-SHA-256$4096:MDEyMzQ1Njc4OWFiY2RlZg==$")
    assert "secret" not in v and "'" not in v


@pytest_asyncio.fixture
async def azure_like_admin():
    if TEST_DATABASE_URL is None:
        pytest.skip("TEST_DATABASE_URL not set")
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    db_name = make_url(TEST_DATABASE_URL).database

    async def drop(conn, role):
        if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).scalar():
            if role == _ROLE:  # its CONNECT was granted by the admin, so only the admin can revoke it
                await conn.execute(text(f"SET ROLE {_ADMIN}"))
                await conn.execute(text(f'REVOKE CONNECT ON DATABASE "{db_name}" FROM {role}'))
                await conn.execute(text("RESET ROLE"))
            await conn.execute(text(f"DROP OWNED BY {role}"))
            await conn.execute(text(f"DROP ROLE {role}"))

    async with engine.begin() as conn:
        await drop(conn, _ROLE)
        exists = (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _ADMIN})).scalar()
        if not exists:
            await conn.execute(text(f"CREATE ROLE {_ADMIN} NOLOGIN CREATEROLE CREATEDB"))
        await conn.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {_ADMIN} WITH GRANT OPTION'))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def as_admin():
        session = factory()
        await session.execute(text(f"SET ROLE {_ADMIN}"))
        return session

    yield as_admin, db_name
    async with engine.begin() as conn:
        await drop(conn, _ROLE)
        await drop(conn, _ADMIN)
    await engine.dispose()


async def _can_login(password: str) -> bool:
    url = make_url(TEST_DATABASE_URL)
    try:
        conn = await asyncpg.connect(
            host=url.host, port=url.port, database=url.database, user=_ROLE, password=password
        )
    except asyncpg.InvalidPasswordError:
        return False
    await conn.close()
    return True


async def test_rerun_as_non_superuser_admin(azure_like_admin):
    as_admin, db_name = azure_like_admin
    for password in ("first-password-1234", "second-password-5678"):  # create, then the update path
        session = await as_admin()
        async with session:
            await ensure_role(session, _ROLE, password, db_name)
    assert await _can_login("second-password-5678")
    assert not await _can_login("first-password-1234")


async def test_failure_message_hides_password(azure_like_admin, monkeypatch):
    as_admin, _db_name = azure_like_admin

    class _Factory:
        async def __aenter__(self):
            self.session = await as_admin()
            return self.session

        async def __aexit__(self, *exc):
            await self.session.close()

    monkeypatch.setattr(ensure_app_role, "async_session_factory", _Factory)
    monkeypatch.setattr(ensure_app_role, "_ROLE", "postgres")  # a superuser: altering it is refused
    monkeypatch.setenv("DMARC_APP_DB_PASSWORD", "do-not-leak-me-123")
    with pytest.raises(SystemExit) as exc_info:
        await ensure_app_role.main()
    message = str(exc_info.value)
    assert "failed" in message
    assert "do-not-leak-me-123" not in message and "SCRAM" not in message
