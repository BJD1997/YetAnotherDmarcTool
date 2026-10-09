"""SETUP PROBLEM log warnings: each mistake is named once, and setups that
are fine (or deliberately split, like Azure) stay quiet."""

import logging

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.services import setup_checks

from tests.conftest import TEST_DATABASE_URL


@pytest.fixture
def clean_settings(monkeypatch):
    for name, value in {
        "public_base_url": "https://dmarc.example.com",
        "entra_sso_client_id": None, "entra_sso_client_secret": None,
        "entra_mail_client_id": None, "entra_mail_client_secret": None,
        "hosted_reports_mailbox_address": None, "hosted_reports_tenant_id": None,
        "cloudflare_api_token": None, "cloudflare_zone_id": None,
        "deployment_platform": None,
        "updater_url": None, "updater_shared_secret": None, "starttls_check_mode": "tls_rpt",
        "fernet_key": Fernet.generate_key().decode(),
        "database_url": "postgresql+asyncpg://dmarc_app:Str0ng-Pw@db:5432/dmarc",
    }.items():
        monkeypatch.setattr(settings, name, value)
    return monkeypatch


def problems() -> str:
    return "\n".join(setup_checks.config_problems())


def test_a_good_setup_has_no_problems(clean_settings):
    assert setup_checks.config_problems() == []


def test_public_base_url(clean_settings):
    clean_settings.setattr(settings, "public_base_url", "http://localhost:8000")
    assert "PUBLIC_BASE_URL is still the default" in problems()
    clean_settings.setattr(settings, "public_base_url", "http://dmarc.example.com")
    assert "PUBLIC_BASE_URL uses http://" in problems()
    clean_settings.setattr(settings, "public_base_url", "http://localhost:5173")
    assert setup_checks.config_problems() == []  # a local test setup is fine


def test_half_set_integrations(clean_settings):
    clean_settings.setattr(settings, "entra_sso_client_id", "abc")
    assert "ENTRA_SSO_CLIENT_SECRET" in problems()
    clean_settings.setattr(settings, "entra_sso_client_id", None)

    clean_settings.setattr(settings, "entra_mail_client_id", "abc")
    assert "ENTRA_MAIL_CLIENT_SECRET" in problems()
    clean_settings.setattr(settings, "deployment_platform", "azure-container-apps")
    assert setup_checks.config_problems() == []  # Azure gives the secret to the worker only
    clean_settings.setattr(settings, "deployment_platform", None)
    clean_settings.setattr(settings, "entra_mail_client_id", None)

    clean_settings.setattr(settings, "hosted_reports_mailbox_address", "reports@hosted.example")
    text_ = problems()
    assert "HOSTED_REPORTS_TENANT_ID" in text_ and "ENTRA_MAIL_CLIENT_ID" in text_
    clean_settings.setattr(settings, "hosted_reports_mailbox_address", None)

    clean_settings.setattr(settings, "cloudflare_api_token", "tok")
    text_ = problems()
    assert "CLOUDFLARE_ZONE_ID" in text_ and "hosted reporting mailbox" in text_


@pytest.mark.parametrize(
    "path, host, client, headers, scheme, expected",
    [
        # proxy in front but not trusted: visitor IP lost
        ("/api/auth/me", "dmarc.example.com", "172.18.0.5", {"x-forwarded-for": "203.0.113.9"}, "http", "FORWARDED_ALLOW_IPS"),
        # trusted proxy: uvicorn already took the visitor's IP from the header
        ("/api/auth/me", "dmarc.example.com", "203.0.113.9", {"x-forwarded-for": "203.0.113.9", "x-forwarded-proto": "https"}, "https", None),
        # no proxy at all, plain http, while PUBLIC_BASE_URL is https
        ("/api/auth/me", "192.168.1.20:8000", "192.168.1.30", {}, "http", "reached directly over http"),
        # containers talking to each other, health checks: not judged
        ("/api/health", "192.168.1.20:8000", "192.168.1.30", {}, "http", None),
        ("/api/auth/me", "api:8000", "172.18.0.7", {}, "http", None),
        ("/api/auth/me", "localhost:8000", "127.0.0.1", {}, "http", None),
    ],
)
def test_request_problems(clean_settings, path, host, client, headers, scheme, expected):
    problem = setup_checks.request_problem(path, host, client, headers, scheme)
    assert (problem is None) if expected is None else (expected in problem)


async def test_worker_running_follows_the_scheduler_lock(migrated_db, monkeypatch):
    monkeypatch.setattr(settings, "leader_lock_key", 0x59414499)
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as checker:
            assert await setup_checks.worker_running(checker) is False
            async with engine.connect() as worker:
                await worker.execute(text("SELECT pg_advisory_lock(:k)"), {"k": 0x59414499})
                assert await setup_checks.worker_running(checker) is True
                await worker.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 0x59414499})
    finally:
        await engine.dispose()


async def test_leftover_bootstrap_password(api, monkeypatch):
    from app.models.platform_admin import PlatformAdmin
    import uuid

    _client, owner_factory = api
    async with owner_factory() as db:
        db.add(PlatformAdmin(email=f"a+{uuid.uuid4()}@platform.example", password_hash="x", is_active=True))
        await db.commit()
    monkeypatch.setattr(settings, "platform_admin_bootstrap_password", "s3cret")
    async with owner_factory() as db:
        found = "\n".join(await setup_checks.database_problems(db))
    assert "PLATFORM_ADMIN_BOOTSTRAP_PASSWORD is still set" in found and "s3cret" not in found


def test_middleware_logs_each_problem_once(clean_settings, caplog):
    setup_checks.reset_request_warnings()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            setup_checks.note_request("/api/auth/me", "192.168.1.20:8000", "192.168.1.30", {}, "http")
    assert sum("reached directly over http" in r.message for r in caplog.records) == 1


def test_updater_secret(clean_settings):
    clean_settings.setattr(settings, "updater_url", "http://updater:9999")
    assert "UPDATER_SHARED_SECRET isn't set" in problems()
    clean_settings.setattr(settings, "updater_shared_secret", "short")
    assert "too short" in problems() and "short" not in problems().split("UPDATER_SHARED_SECRET")[0]
    clean_settings.setattr(settings, "updater_shared_secret", "x" * 32)
    assert setup_checks.config_problems() == []
    clean_settings.setattr(settings, "updater_url", None)
    clean_settings.setattr(settings, "updater_shared_secret", None)
    assert setup_checks.config_problems() == []  # Portainer and Azure update another way


def test_tips_for_optional_integrations(clean_settings):
    tips = "\n".join(setup_checks.config_tips())
    for name in ("ENTRA_SSO_CLIENT_ID", "ENTRA_MAIL_CLIENT_ID", "HOSTED_REPORTS_MAILBOX_ADDRESS"):
        assert name in tips
    assert "CLOUDFLARE" not in tips  # only useful with the hosted mailbox

    for name, value in {
        "entra_sso_client_id": "a", "entra_sso_client_secret": "b",
        "entra_mail_client_id": "c", "entra_mail_client_secret": "d",
        "hosted_reports_mailbox_address": "reports@hosted.example", "hosted_reports_tenant_id": "t",
    }.items():
        clean_settings.setattr(settings, name, value)
    assert "CLOUDFLARE_API_TOKEN" in "\n".join(setup_checks.config_tips())

    clean_settings.setattr(settings, "cloudflare_api_token", "tok")
    clean_settings.setattr(settings, "cloudflare_zone_id", "zone")
    assert setup_checks.config_tips() == []


def test_starttls_probe_on_azure(clean_settings):
    clean_settings.setattr(settings, "starttls_check_enabled", True)
    clean_settings.setattr(settings, "starttls_check_mode", "probe")
    clean_settings.setattr(settings, "deployment_platform", "azure-container-apps")
    assert "STARTTLS_CHECK_MODE=tls_rpt" in problems()
    clean_settings.setattr(settings, "starttls_check_mode", "tls_rpt")
    assert setup_checks.config_problems() == []


def test_portainer_updates_are_optional(clean_settings):
    clean_settings.setattr(settings, "deployment_platform", "portainer")
    clean_settings.setattr(settings, "updater_url", "http://updater:9999")
    assert setup_checks.config_problems() == []
    assert "PORTAINER_API_KEY" in "\n".join(setup_checks.config_tips())

    clean_settings.setattr(settings, "updater_shared_secret", "x" * 32)
    assert not any("PORTAINER" in tip for tip in setup_checks.config_tips())


@pytest.mark.parametrize(
    "url,variable",
    [
        ("postgresql+asyncpg://dmarc:dmarc@db:5432/dmarc", "POSTGRES_PASSWORD"),
        ("postgresql+asyncpg://dmarc_app:dmarc_app@db:5432/dmarc", "DMARC_APP_DB_PASSWORD"),
    ],
)
def test_example_database_passwords_are_flagged(url, variable):
    problem = setup_checks.example_database_login_problem(url)

    assert problem is not None and variable in problem and "ALTER ROLE" in problem


def test_own_database_password_is_fine():
    assert setup_checks.example_database_login_problem("postgresql+asyncpg://dmarc_app:Str0ng-Pw@db:5432/dmarc") is None
