"""Self-service password/MFA changes (/auth/change-password, /auth/mfa/reset/*),
org-admin credential resets (/users/{id}/reset-*), and granting operator
(admin console) access to organizations."""

import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.models.enums import AuthMethod, UserRole, UserStatus
from app.models.organization import Organization
from app.models.session import UserSession
from app.models.user import User
from app.services.auth import session_manager
from app.services.auth.password import hash_password
from app.services.crypto import secrets as crypto_secrets
from tests.conftest import login_as, login_as_platform_admin, seed_org_and_user

PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "an entirely new passphrase"


@pytest.fixture(autouse=True)
def _fernet_key_for_totp_encryption(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


async def _seed_enrolled_user(owner_factory, org, *, email="teammate@test.example", role=UserRole.member):
    secret = pyotp.random_base32()
    async with owner_factory() as db:
        user = User(
            organization_id=org.id,
            email=email,
            role=role,
            status=UserStatus.active,
            auth_method=AuthMethod.local,
            password_hash=hash_password(PASSWORD),
            otp_secret=secret,
            otp_enrolled_at=datetime.now(timezone.utc),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        await db.commit()
    return user, secret


async def _extra_session(owner_factory, user) -> None:
    """A second signed-in device for `user`, to check it gets signed out."""
    async with owner_factory() as db:
        await session_manager.create_user_session(
            db, user_id=user.id, organization_id=user.organization_id, ip_address="10.0.0.2", user_agent="other"
        )
        await db.commit()


async def _active_session_count(owner_factory, user) -> int:
    async with owner_factory() as db:
        rows = await db.execute(
            select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        )
        return len(rows.scalars().all())


async def _password_login(client, email, password):
    client.cookies.clear()
    return await client.post("/api/auth/local-login", json={"email": email, "password": password})


# ---------- self-service ----------


async def test_change_password_requires_current_and_signs_out_other_devices(api):
    client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)
    user, _secret = await _seed_enrolled_user(owner_factory, org)
    await _extra_session(owner_factory, user)
    await login_as(client, owner_factory, user)

    wrong = await client.post(
        "/api/auth/change-password", json={"current_password": "not it at all", "new_password": NEW_PASSWORD}
    )
    assert wrong.status_code == 400
    too_short = await client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"})
    assert too_short.status_code == 422

    ok = await client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert ok.status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 200  # this device stays signed in
    assert await _active_session_count(owner_factory, user) == 1

    assert (await _password_login(client, user.email, PASSWORD)).status_code == 401
    assert (await _password_login(client, user.email, NEW_PASSWORD)).status_code == 200


async def test_sso_user_cannot_use_local_credential_endpoints(api):
    client, owner_factory = api
    _org, entra_user = await seed_org_and_user(owner_factory, entra=True)
    await login_as(client, owner_factory, entra_user)

    assert (await client.get("/api/auth/me")).json()["auth_method"] == "entra"
    change = await client.post("/api/auth/change-password", json={"current_password": "x", "new_password": NEW_PASSWORD})
    assert change.status_code == 409
    assert (await client.post("/api/auth/mfa/reset/start", json={"current_password": "x"})).status_code == 409


async def test_replace_authenticator(api):
    client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)
    user, old_secret = await _seed_enrolled_user(owner_factory, org)
    await _extra_session(owner_factory, user)
    await login_as(client, owner_factory, user)

    assert (await client.post("/api/auth/mfa/reset/start", json={"current_password": "wrong"})).status_code == 400
    start = await client.post("/api/auth/mfa/reset/start", json={"current_password": PASSWORD})
    assert start.status_code == 200
    new_secret = start.json()["secret"]

    bad_code = await client.post(
        "/api/auth/mfa/reset/confirm", json={"current_password": PASSWORD, "secret": new_secret, "code": "000000"}
    )
    assert bad_code.status_code == 400
    confirm = await client.post(
        "/api/auth/mfa/reset/confirm",
        json={"current_password": PASSWORD, "secret": new_secret, "code": pyotp.TOTP(new_secret).now()},
    )
    assert confirm.status_code == 200
    assert len(confirm.json()["recovery_codes"]) == 10
    assert await _active_session_count(owner_factory, user) == 1

    # Next sign-in wants a code from the NEW authenticator, not the old one.
    assert (await _password_login(client, user.email, PASSWORD)).json() == {"needs_enrollment": False}
    assert (await client.post("/api/auth/verify-otp", json={"code": pyotp.TOTP(old_secret).now()})).status_code == 401
    assert (await client.post("/api/auth/verify-otp", json={"code": pyotp.TOTP(new_secret).now()})).status_code == 204


# ---------- org-admin resets ----------


async def test_admin_password_reset_kills_old_password_and_keeps_mfa(api):
    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    user, secret = await _seed_enrolled_user(owner_factory, org)
    await _extra_session(owner_factory, user)
    await login_as(client, owner_factory, admin)

    members = (await client.get("/api/users")).json()
    assert next(m for m in members if m["id"] == str(user.id))["mfa_enrolled"] is True

    reset = await client.post(f"/api/users/{user.id}/reset-password")
    assert reset.status_code == 200
    token = parse_qs(urlparse(reset.json()["setup_link"]).query)["token"][0]
    assert await _active_session_count(owner_factory, user) == 0
    assert (await _password_login(client, user.email, PASSWORD)).status_code == 401

    set_pw = await client.post("/api/auth/set-password", json={"token": token, "new_password": NEW_PASSWORD})
    assert set_pw.status_code == 200
    assert set_pw.json() == {"needs_enrollment": False}
    assert (await client.post("/api/auth/verify-otp", json={"code": pyotp.TOTP(secret).now()})).status_code == 204


async def test_second_password_reset_voids_the_first_link(api):
    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    user, _ = await _seed_enrolled_user(owner_factory, org)
    await login_as(client, owner_factory, admin)

    first = (await client.post(f"/api/users/{user.id}/reset-password")).json()["setup_link"]
    await client.post(f"/api/users/{user.id}/reset-password")
    token = parse_qs(urlparse(first).query)["token"][0]

    client.cookies.clear()
    assert (await client.post("/api/auth/set-password", json={"token": token, "new_password": NEW_PASSWORD})).status_code == 400


async def test_admin_mfa_reset_sends_user_back_to_enrollment(api):
    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    user, _ = await _seed_enrolled_user(owner_factory, org)
    await _extra_session(owner_factory, user)
    await login_as(client, owner_factory, admin)

    assert (await client.post(f"/api/users/{user.id}/reset-mfa")).status_code == 204
    assert await _active_session_count(owner_factory, user) == 0

    assert (await _password_login(client, user.email, PASSWORD)).json() == {"needs_enrollment": True}
    new_secret = (await client.post("/api/auth/enroll-otp")).json()["secret"]
    confirm = await client.post(
        "/api/auth/enroll-otp/confirm", json={"secret": new_secret, "code": pyotp.TOTP(new_secret).now()}
    )
    assert confirm.status_code == 200


@pytest.mark.parametrize("endpoint", ["reset-password", "reset-mfa"])
async def test_admin_resets_are_guarded(api, endpoint):
    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    member, _ = await _seed_enrolled_user(owner_factory, org)
    other_org, _ = await seed_org_and_user(owner_factory, entra=True)
    stranger, _ = await _seed_enrolled_user(owner_factory, other_org, email="stranger@test.example")
    async with owner_factory() as db:
        sso_user = User(
            organization_id=org.id, email="sso@test.example", role=UserRole.member,
            status=UserStatus.active, auth_method=AuthMethod.entra, entra_object_id=str(uuid.uuid4()),
        )
        db.add(sso_user)
        await db.commit()

    await login_as(client, owner_factory, member)
    assert (await client.post(f"/api/users/{admin.id}/{endpoint}")).status_code == 403

    await login_as(client, owner_factory, admin)
    assert (await client.post(f"/api/users/{admin.id}/{endpoint}")).status_code == 400
    assert (await client.post(f"/api/users/{sso_user.id}/{endpoint}")).status_code == 409
    assert (await client.post(f"/api/users/{stranger.id}/{endpoint}")).status_code == 404


# ---------- operator orgs ----------


async def test_several_orgs_can_have_operator_access(api):
    client, owner_factory = api
    org_a, admin_a = await seed_org_and_user(owner_factory)
    org_b, admin_b = await seed_org_and_user(owner_factory, entra=True)
    await login_as_platform_admin(client, owner_factory)

    for org in (org_a, org_b):
        response = await client.patch(f"/api/admin/organizations/{org.id}", json={"is_operator": True})
        assert response.status_code == 200
        assert response.json()["is_operator"] is True

    for admin in (admin_a, admin_b):
        client.cookies.clear()
        await login_as(client, owner_factory, admin)
        assert (await client.get("/api/admin/me")).json()["auth_type"] == "operator_org"


async def test_operator_admin_cannot_revoke_own_orgs_access(api):
    client, owner_factory = api
    org_a, admin_a = await seed_org_and_user(owner_factory)
    org_b, _ = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        for org_id in (org_a.id, org_b.id):
            (await db.get(Organization, org_id)).is_operator = True
        await db.commit()
    await login_as(client, owner_factory, admin_a)

    own = await client.patch(f"/api/admin/organizations/{org_a.id}", json={"is_operator": False})
    assert own.status_code == 400
    other = await client.patch(f"/api/admin/organizations/{org_b.id}", json={"is_operator": False})
    assert other.status_code == 200
    assert other.json()["is_operator"] is False
