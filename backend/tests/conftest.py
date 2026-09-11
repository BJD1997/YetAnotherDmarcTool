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
import uuid
from datetime import datetime, timezone
from pathlib import Path

import app.middleware.demo_read_only as demo_read_only_module
import app.workers.jobs.mailbox_poll_job as mailbox_poll_job_module
import httpx
import pytest
import pytest_asyncio
from app.db import session as session_module
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.session import get_db
from app.main import app
from app.models.enums import AuthMethod, OrganizationStatus, UserRole, UserStatus
from app.models.organization import Organization
from app.models.user import User
from app.services.auth import session_manager
from app.services.auth.rate_limit import login_limiter, otp_limiter

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
        await owner.execute(text("DELETE FROM rate_limit_hits"))
        await owner.commit()
        async with app_factory() as app:
            yield owner, app
    await app_engine.dispose()
    await owner_engine.dispose()


@pytest.fixture
def app_db_url(migrated_db) -> str:
    """The dmarc_app (non-owner) async URL — used by tests that build their own
    engine(s), e.g. leader-election contention."""
    return _app_url()


@pytest_asyncio.fixture
async def app_sessionmaker(migrated_db):
    """A sessionmaker on the non-owner dmarc_app role (the role the worker runs
    as), with the worker/infra tables emptied first. Lets a test open several
    concurrent sessions — e.g. to prove FOR UPDATE SKIP LOCKED never
    double-claims a job."""
    engine = create_async_engine(_app_url(), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM background_jobs"))
        await conn.execute(text("DELETE FROM rate_limit_hits"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


CSRF_HEADERS = {"X-Requested-With": "yetanotherdmarctool"}


@pytest_asyncio.fixture
async def api(migrated_db):
    """Yields (client, owner_factory) for HTTP-level router tests. `client` is
    an httpx.AsyncClient wired directly to the real ASGI app (no network
    socket) via ASGITransport, with the CSRF header pre-set (see
    enforce_csrf_header in app/middleware/csrf.py) so POST/PUT/PATCH/DELETE
    calls don't need to set it per-test. Requests run through app.db.session.get_db
    overridden to connect as the non-owner dmarc_app role — the same role
    FORCE ROW LEVEL SECURITY binds in prod — so RLS is genuinely exercised,
    not bypassed. Also patches async_session_factory in session_module,
    demo_read_only_module, AND mailbox_poll_job_module (plus mailbox_poll_job_module's
    own copy of `engine`), since each of those modules imports directly from
    app.db.session rather than going through dependency injection:
    demo_read_only's enforce_demo_read_only middleware, and mailbox_poll_job's
    poll_org_mailbox — which set_mailbox_connection/resync_mailbox_connection
    dispatch as a real BackgroundTask that DOES execute under ASGITransport,
    so without this patch it would try to reach the prod database.
    `owner_factory` is for test setup that must bypass RLS (seeding orgs/users
    directly), same superuser role rls_sessions uses. Also clears the shared
    `login_limiter`/`otp_limiter` in-memory rate-limit state at the start of
    every test — both are module-level singletons (see `rate_limit.py`), so
    without this reset the 11th test in a session hitting any rate-limited
    auth endpoint from the same simulated client IP gets a spurious 429.
    """
    login_limiter._hits.clear()
    otp_limiter._hits.clear()

    owner_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    app_engine = create_async_engine(_app_url(), poolclass=NullPool)
    owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)

    async with owner_factory() as owner:
        await owner.execute(text("TRUNCATE organizations CASCADE"))
        await owner.execute(text("DELETE FROM rate_limit_hits"))
        await owner.commit()

    async def _override_get_db():
        async with app_factory() as session:
            yield session

    # Override both the dependency-injected get_db and the global async_session_factory
    # (used by middlewares like enforce_demo_read_only that don't use dependency injection).
    # Must replace in session_module, demo_read_only_module, AND mailbox_poll_job_module
    # since app.middleware.demo_read_only and app.workers.jobs.mailbox_poll_job both
    # imported it directly. mailbox_poll_job also imports `engine` directly (for its
    # advisory-lock connection), so that needs patching here too.
    app.dependency_overrides[get_db] = _override_get_db
    original_session_factory = session_module.async_session_factory
    original_demo_read_only_factory = demo_read_only_module.async_session_factory
    original_mailbox_poll_job_factory = mailbox_poll_job_module.async_session_factory
    original_mailbox_poll_job_engine = mailbox_poll_job_module.engine
    session_module.async_session_factory = app_factory
    demo_read_only_module.async_session_factory = app_factory
    mailbox_poll_job_module.async_session_factory = app_factory
    mailbox_poll_job_module.engine = app_engine

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=CSRF_HEADERS) as client:
        yield client, owner_factory

    app.dependency_overrides.pop(get_db, None)
    session_module.async_session_factory = original_session_factory
    demo_read_only_module.async_session_factory = original_demo_read_only_factory
    mailbox_poll_job_module.async_session_factory = original_mailbox_poll_job_factory
    mailbox_poll_job_module.engine = original_mailbox_poll_job_engine
    await app_engine.dispose()
    await owner_engine.dispose()


async def seed_org_and_user(
    owner_factory,
    *,
    role: UserRole = UserRole.org_admin,
    entra: bool = False,
    is_demo_read_only: bool = False,
) -> tuple[Organization, User]:
    """Inserts an Organization + User directly via the RLS-bypassing owner
    role — same idea as _seed_two_orgs in test_rls.py."""
    async with owner_factory() as db:
        org = Organization(
            name="Test Org",
            status=OrganizationStatus.active,
            entra_tenant_id=uuid.uuid4() if entra else None,
            is_demo_read_only=is_demo_read_only,
        )
        db.add(org)
        await db.flush()
        user = User(
            organization_id=org.id,
            email="admin@test.example",
            display_name="Test Admin",
            role=role,
            status=UserStatus.active,
            auth_method=AuthMethod.entra if entra else AuthMethod.local,
        )
        db.add(user)
        await db.flush()
        await db.refresh(org)
        await db.refresh(user)
        await db.commit()
        return org, user


async def login_as(client: httpx.AsyncClient, owner_factory, user: User) -> None:
    """Mints a real session (via the same session_manager the app uses) and
    sets it as a cookie on `client` — user_sessions is RLS-exempt, so the
    owner role is fine here."""
    async with owner_factory() as db:
        _, raw_token = await session_manager.create_user_session(
            db, user_id=user.id, organization_id=user.organization_id, ip_address="127.0.0.1", user_agent="pytest",
        )
        await db.commit()
    client.cookies.set(settings.session_cookie_name, raw_token)


async def login_as_platform_admin(client: httpx.AsyncClient, owner_factory) -> None:
    """Creates a local PlatformAdmin and logs the client in as them. password_hash
    is a placeholder, not a real hash — this mints a session directly via
    session_manager, the same bypass a real password login would produce,
    without ever calling verify_password, so the placeholder is never checked."""
    from app.models.platform_admin import PlatformAdmin

    async with owner_factory() as db:
        admin = PlatformAdmin(
            email=f"admin+{uuid.uuid4()}@platform.example",
            password_hash="unused",
            is_active=True,
        )
        db.add(admin)
        await db.flush()
        _, raw_token = await session_manager.create_platform_admin_session(
            db, platform_admin_id=admin.id, ip_address="127.0.0.1", user_agent="pytest",
        )
        await db.commit()
    client.cookies.set(settings.platform_admin_session_cookie_name, raw_token)


async def seed_platform_admin_with_totp(owner_factory) -> tuple:
    """Creates a local PlatformAdmin WITH a TOTP secret already enrolled
    (unlike login_as_platform_admin, which logs straight in with no MFA
    step at all) — returns (admin, secret) so tests can compute valid
    codes with pyotp.TOTP(secret).now(). Requires an active FERNET_KEY in
    settings (see test_platform_admin.py's `_fernet_key_for_totp_encryption`
    autouse fixture) — otp_secret is a Fernet-encrypted column and fails
    closed without one."""
    import pyotp

    from app.models.platform_admin import PlatformAdmin
    from app.services.auth.password import hash_password

    secret = pyotp.random_base32()
    async with owner_factory() as db:
        admin = PlatformAdmin(
            email=f"admin-totp+{uuid.uuid4()}@platform.example",
            password_hash=hash_password("correct horse battery staple"),
            is_active=True,
            otp_secret=secret,
            otp_enrolled_at=datetime.now(timezone.utc),
        )
        db.add(admin)
        await db.flush()
        await db.refresh(admin)
        await db.commit()
        return admin, secret
