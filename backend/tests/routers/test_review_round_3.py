"""Third review round: single-use MFA codes, break-glass sign-in logging and
session revocation, API docs off by default."""

from datetime import datetime, timedelta

import pyotp
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.models.enums import AuthMethod, UserRole, UserStatus
from app.models.platform_admin_session import PlatformAdminSession
from app.models.user import User
from app.services.auth import session_manager
from app.services.auth.password import hash_password
from app.services.crypto import secrets as crypto_secrets
from tests.conftest import login_as, seed_org_and_user, seed_platform_admin_with_totp

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _fernet_key_for_totp_encryption(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


def _next_code(secret: str) -> str:
    """The code for the next 30-second step — still inside the accepted
    window, but a step the tests haven't used yet."""
    return pyotp.TOTP(secret).at(datetime.now() + timedelta(seconds=30))


async def test_an_mfa_code_works_only_once(api):
    client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)
    secret = pyotp.random_base32()
    async with owner_factory() as db:
        db.add(User(
            organization_id=org.id, email="once@test.example", role=UserRole.member, status=UserStatus.active,
            auth_method=AuthMethod.local, password_hash=hash_password(PASSWORD), otp_secret=secret,
            otp_enrolled_at=datetime.now(),
        ))
        await db.commit()
    code = pyotp.TOTP(secret).now()

    await client.post("/api/auth/local-login", json={"email": "once@test.example", "password": PASSWORD})
    assert (await client.post("/api/auth/verify-otp", json={"code": code})).status_code == 204

    await client.post("/api/auth/logout")
    await client.post("/api/auth/local-login", json={"email": "once@test.example", "password": PASSWORD})
    assert (await client.post("/api/auth/verify-otp", json={"code": code})).status_code == 401
    assert (await client.post("/api/auth/verify-otp", json={"code": _next_code(secret)})).status_code == 204


async def test_the_enrollment_code_cannot_also_sign_in(api):
    client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(User(
            organization_id=org.id, email="enroll@test.example", role=UserRole.member, status=UserStatus.active,
            auth_method=AuthMethod.local, password_hash=hash_password(PASSWORD),
        ))
        await db.commit()
    await client.post("/api/auth/local-login", json={"email": "enroll@test.example", "password": PASSWORD})
    secret = (await client.post("/api/auth/enroll-otp")).json()["secret"]
    code = pyotp.TOTP(secret).now()
    assert (await client.post("/api/auth/enroll-otp/confirm", json={"secret": secret, "code": code})).status_code == 200

    await client.post("/api/auth/logout")
    await client.post("/api/auth/local-login", json={"email": "enroll@test.example", "password": PASSWORD})
    assert (await client.post("/api/auth/verify-otp", json={"code": code})).status_code == 401


async def test_break_glass_codes_are_single_use_and_sign_ins_are_logged(api):
    client, owner_factory = api
    admin, secret = await seed_platform_admin_with_totp(owner_factory)
    code = pyotp.TOTP(secret).now()

    await client.post("/api/admin/login", json={"email": admin.email, "password": "wrong password here"})
    await client.post("/api/admin/login", json={"email": admin.email, "password": PASSWORD})
    assert (await client.post("/api/admin/verify-otp", json={"code": code})).status_code == 204
    await client.post("/api/admin/logout")
    await client.post("/api/admin/login", json={"email": admin.email, "password": PASSWORD})
    assert (await client.post("/api/admin/verify-otp", json={"code": code})).status_code == 401
    assert (await client.post("/api/admin/verify-otp", json={"code": _next_code(secret)})).status_code == 204

    events = (await client.get("/api/admin/sign-in-events")).json()["events"]
    mine = [(e["result"], e["failure_reason"]) for e in events if e["email"] == admin.email]
    assert mine == [
        ("success", None),
        ("failure", "invalid_totp_or_recovery_code"),
        ("success", None),
        ("failure", "invalid_credentials"),
    ]
    assert all(e["auth_method"] == "platform_admin" for e in events)


async def test_break_glass_events_stay_out_of_organizations_logs(api):
    client, owner_factory = api
    admin, _secret = await seed_platform_admin_with_totp(owner_factory)
    await client.post("/api/admin/login", json={"email": admin.email, "password": "wrong password here"})

    _org, org_admin = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, org_admin)

    events = (await client.get("/api/sign-in-events")).json()["events"]
    assert all(e["auth_method"] != "platform_admin" for e in events)


async def test_admin_password_change_signs_out_other_admin_sessions(api):
    client, owner_factory = api
    admin, _secret = await seed_platform_admin_with_totp(owner_factory)
    async with owner_factory() as db:
        _other, _ = await session_manager.create_platform_admin_session(db, platform_admin_id=admin.id, ip_address=None, user_agent="other")
        _mine, raw = await session_manager.create_platform_admin_session(db, platform_admin_id=admin.id, ip_address=None, user_agent="mine")
        await db.commit()
    client.cookies.set(settings.platform_admin_session_cookie_name, raw)

    response = await client.post(
        "/api/admin/change-password", json={"current_password": PASSWORD, "new_password": "a brand new password here"}
    )

    assert response.status_code == 204
    async with owner_factory() as db:
        active = (
            await db.execute(
                select(PlatformAdminSession.user_agent).where(
                    PlatformAdminSession.platform_admin_id == admin.id, PlatformAdminSession.revoked_at.is_(None)
                )
            )
        ).scalars().all()
    assert active == ["mine"]
    events = (await client.get("/api/admin/sign-in-events?result=account_change")).json()["events"]
    assert [e["failure_reason"] for e in events if e["email"] == admin.email] == ["password_changed"]


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_are_off_by_default(api, path):
    client, _owner_factory = api
    response = await client.get(path)
    assert "swagger" not in response.text.lower()
    assert '"openapi"' not in response.text
