"""Break-glass session limits, and choosing between a break-glass session and
an operator org's session when the browser holds both."""

from datetime import datetime, timedelta, timezone

import pyotp
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.models.organization import Organization
from app.models.platform_admin_session import PlatformAdminSession
from app.services.crypto import secrets as crypto_secrets
from tests.conftest import login_as, login_as_platform_admin, seed_org_and_user, seed_platform_admin_with_totp

CHOICE = settings.platform_admin_choice_cookie_name


@pytest.fixture(autouse=True)
def _fernet_key_for_totp_encryption(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


async def _operator_admin(owner_factory):
    org, admin = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        (await db.get(Organization, org.id)).is_operator = True
        await db.commit()
    return org, admin


# ---------- break-glass session limits ----------


async def test_break_glass_session_idles_out_after_four_hours(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    assert (await client.get("/api/admin/me")).status_code == 200
    async with owner_factory() as db:
        session = (
            await db.execute(select(PlatformAdminSession).order_by(PlatformAdminSession.created_at.desc()).limit(1))
        ).scalar_one()
        remaining = session.expires_at - datetime.now(timezone.utc)
        assert timedelta(hours=3, minutes=59) < remaining <= timedelta(hours=4)

        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    assert (await client.get("/api/admin/me")).status_code == 401


async def test_break_glass_session_ends_after_24_hours_even_when_active(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    async with owner_factory() as db:
        session = (
            await db.execute(select(PlatformAdminSession).order_by(PlatformAdminSession.created_at.desc()).limit(1))
        ).scalar_one()
        session.created_at = datetime.now(timezone.utc) - timedelta(hours=24, minutes=1)
        session.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)  # still "active"
        await db.commit()

    assert (await client.get("/api/admin/me")).status_code == 401


# ---------- choosing a sign-in ----------


async def test_both_sessions_means_the_console_asks(api):
    client, owner_factory = api
    org, operator_admin = await _operator_admin(owner_factory)
    await login_as(client, owner_factory, operator_admin)
    await login_as_platform_admin(client, owner_factory)

    assert (await client.get("/api/admin/me")).status_code == 409
    # The admin API as a whole refuses to guess, not just /me.
    assert (await client.get("/api/admin/organizations")).status_code == 409

    options = (await client.get("/api/admin/session-options")).json()
    assert options["operator_org"] == {"email": operator_admin.email, "organization_name": org.name}
    assert options["local"]["email"].endswith("@platform.example")


async def test_choosing_the_organization_sign_in(api):
    client, owner_factory = api
    org, operator_admin = await _operator_admin(owner_factory)
    await login_as(client, owner_factory, operator_admin)
    await login_as_platform_admin(client, owner_factory)

    assert (await client.post("/api/admin/session-choice", json={"choice": "operator_org"})).status_code == 204
    me = (await client.get("/api/admin/me")).json()
    assert (me["auth_type"], me["email"], me["organization_name"], me["can_switch"]) == (
        "operator_org", operator_admin.email, org.name, True,
    )
    # The break-glass-only endpoint stays tied to the break-glass session.
    assert (await client.post("/api/admin/session-choice", json={"choice": "local"})).status_code == 204
    assert (await client.get("/api/admin/me")).json()["auth_type"] == "local"

    # Clearing the choice asks again.
    cleared = await client.post("/api/admin/session-choice", json={"choice": None})
    assert cleared.status_code == 204
    assert CHOICE not in client.cookies
    assert (await client.get("/api/admin/me")).status_code == 409


async def test_cannot_choose_a_sign_in_the_browser_doesnt_hold(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.post("/api/admin/session-choice", json={"choice": "operator_org"})
    assert response.status_code == 400
    me = (await client.get("/api/admin/me")).json()
    assert (me["auth_type"], me["can_switch"]) == ("local", False)


async def test_break_glass_login_counts_as_choosing_it(api):
    client, owner_factory = api
    _org, operator_admin = await _operator_admin(owner_factory)
    await login_as(client, owner_factory, operator_admin)
    admin, secret = await seed_platform_admin_with_totp(owner_factory)

    await client.post("/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"})
    verify = await client.post("/api/admin/verify-otp", json={"code": pyotp.TOTP(secret).now()})

    assert verify.status_code == 204
    assert verify.cookies.get(CHOICE) == "local"
    assert (await client.get("/api/admin/me")).json()["auth_type"] == "local"

    # Signing out of break-glass forgets the choice; the org sign-in remains.
    logout = await client.post("/api/admin/logout")
    assert logout.status_code == 204
    assert (await client.get("/api/admin/me")).json()["auth_type"] == "operator_org"
