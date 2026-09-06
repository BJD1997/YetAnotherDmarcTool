from tests.conftest import login_as, login_as_platform_admin, seed_org_and_user


async def test_auth_config_sso_disabled(api):
    client, _owner_factory = api
    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {"entra_sso_enabled": False}


async def test_login_redirect_404_when_sso_disabled(api):
    client, _owner_factory = api
    response = await client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 404


async def test_me_requires_auth(api):
    client, _owner_factory = api
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


async def test_me_returns_current_user(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == user.email
    assert body["organization_id"] == str(org.id)


async def test_logout_clears_session(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    logout_response = await client.post("/api/auth/logout")
    assert logout_response.status_code == 204

    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 401


from urllib.parse import parse_qs, urlparse

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.services.auth.password import hash_password
from app.services.crypto import secrets as crypto_secrets


@pytest.fixture(autouse=True)
def _fernet_key_for_totp_encryption(monkeypatch):
    """User.otp_secret is transparently encrypted at rest (see
    app/services/auth/totp_secret.py) and fails closed without a FERNET_KEY —
    same requirement production has. The enroll-otp/confirm flow below writes
    that column for real, so it needs one; same monkeypatch pattern as
    tests/services/test_totp_secret.py's `fernet_key` fixture, just autouse
    here since most tests in this module exercise that path."""
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


async def _seed_local_user_with_password(owner_factory, org, *, password: str = "correct horse battery staple"):
    async with owner_factory() as db:
        user = User(
            organization_id=org.id,
            email="local-login-test@example.com",
            role=UserRole.member,
            status=UserStatus.active,
            auth_method="local",
            password_hash=hash_password(password),
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        await db.commit()
        return user


async def test_local_login_invalid_credentials_wrong_password(api):
    client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=False)
    local_user = await _seed_local_user_with_password(owner_factory, org)

    response = await client.post(
        "/api/auth/local-login", json={"email": local_user.email, "password": "wrong password entirely"}
    )

    assert response.status_code == 401


async def test_local_login_unknown_email(api):
    client, owner_factory = api
    _org, _user = await seed_org_and_user(owner_factory)

    response = await client.post(
        "/api/auth/local-login", json={"email": "nobody-here@example.com", "password": "whatever"}
    )

    assert response.status_code == 401


async def test_local_login_needs_enrollment_when_no_totp(api):
    client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=False)
    local_user = await _seed_local_user_with_password(owner_factory, org)

    response = await client.post(
        "/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"}
    )

    assert response.status_code == 200
    assert response.json() == {"needs_enrollment": True}
    assert "dmarc_mfa_pending" in response.cookies or any(
        "mfa_pending" in c for c in response.cookies
    )


async def test_full_local_login_enroll_and_verify_flow(api):
    """Exercises the whole chain: local-login -> enroll-otp -> enroll-otp/confirm
    -> logout -> local-login again -> verify-otp, proving a real session is
    reachable end to end and that a second login correctly demands the code
    from the now-enrolled secret."""
    client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=False)
    local_user = await _seed_local_user_with_password(owner_factory, org)

    login_response = await client.post(
        "/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"}
    )
    assert login_response.status_code == 200
    assert login_response.json()["needs_enrollment"] is True

    enroll_response = await client.post("/api/auth/enroll-otp")
    assert enroll_response.status_code == 200
    secret = enroll_response.json()["secret"]

    confirm_response = await client.post(
        "/api/auth/enroll-otp/confirm", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )
    assert confirm_response.status_code == 200
    assert len(confirm_response.json()["recovery_codes"]) == 10

    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == local_user.email

    await client.post("/api/auth/logout")

    second_login = await client.post(
        "/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"}
    )
    assert second_login.status_code == 200
    assert second_login.json()["needs_enrollment"] is False

    verify_response = await client.post("/api/auth/verify-otp", json={"code": pyotp.TOTP(secret).now()})
    assert verify_response.status_code == 204

    final_me = await client.get("/api/auth/me")
    assert final_me.status_code == 200


async def test_verify_otp_rejects_wrong_code(api):
    client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=False)
    local_user = await _seed_local_user_with_password(owner_factory, org)
    await client.post("/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"})
    secret = (await client.post("/api/auth/enroll-otp")).json()["secret"]
    await client.post("/api/auth/enroll-otp/confirm", json={"secret": secret, "code": pyotp.TOTP(secret).now()})
    await client.post("/api/auth/logout")
    await client.post("/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"})

    response = await client.post("/api/auth/verify-otp", json={"code": "000000"})

    assert response.status_code == 401


async def test_verify_otp_accepts_recovery_code_once(api):
    """A recovery code is a full TOTP bypass — the security-relevant branch
    here is that it actually works once and is burned after use, not just
    that a wrong code is rejected (see test_verify_otp_rejects_wrong_code)."""
    client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=False)
    local_user = await _seed_local_user_with_password(owner_factory, org)
    await client.post("/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"})
    secret = (await client.post("/api/auth/enroll-otp")).json()["secret"]
    confirm_response = await client.post(
        "/api/auth/enroll-otp/confirm", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )
    recovery_code = confirm_response.json()["recovery_codes"][0]
    await client.post("/api/auth/logout")
    await client.post("/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"})

    first_use = await client.post("/api/auth/verify-otp", json={"code": recovery_code})
    assert first_use.status_code == 204

    await client.post("/api/auth/logout")
    await client.post("/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"})
    second_use = await client.post("/api/auth/verify-otp", json={"code": recovery_code})
    assert second_use.status_code == 401


async def test_verify_otp_no_pending_challenge(api):
    client, _owner_factory = api
    response = await client.post("/api/auth/verify-otp", json={"code": "123456"})
    assert response.status_code == 401


async def test_local_login_demo_read_only_skips_mfa(api):
    client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=False, is_demo_read_only=True)
    local_user = await _seed_local_user_with_password(owner_factory, org)

    response = await client.post(
        "/api/auth/local-login", json={"email": local_user.email, "password": "correct horse battery staple"}
    )

    assert response.status_code == 204

    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 200


async def test_set_password_success_and_replay_rejected(api):
    """Exercises the real set-password flow end to end: a platform admin
    provisions a local user (POST /api/admin/organizations/{org_id}/users,
    already covered from the admin side by test_create_local_user_for_org in
    test_platform_admin.py), the returned setup_link's token is redeemed
    against /api/auth/set-password, and a second redemption of the same
    token is rejected (the `used_at is not None` branch)."""
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Local Auth Org"})).json()["id"]

    create_response = await client.post(
        f"/api/admin/organizations/{org_id}/users", json={"email": "new-local-user@example.com"}
    )
    assert create_response.status_code == 201
    setup_link = create_response.json()["setup_link"]
    token = parse_qs(urlparse(setup_link).query)["token"][0]

    first_use = await client.post(
        "/api/auth/set-password", json={"token": token, "new_password": "a brand new password entirely"}
    )
    assert first_use.status_code == 200
    assert first_use.json() == {"needs_enrollment": True}
    assert "dmarc_mfa_pending" in first_use.cookies or any("mfa_pending" in c for c in first_use.cookies)

    second_use = await client.post(
        "/api/auth/set-password", json={"token": token, "new_password": "a different password entirely"}
    )
    assert second_use.status_code == 400


async def _mock_entra_success(monkeypatch, *, tenant_id: str, object_id: str, email: str, name: str = "Test User"):
    async def _fake_exchange(**kwargs):
        return {"id_token": "fake-id-token", "access_token": "fake-access-token"}

    async def _fake_validate(id_token):
        return {"tid": tenant_id, "oid": object_id, "preferred_username": email, "name": name}

    monkeypatch.setattr("app.routers.auth.entra_oidc.exchange_code_for_tokens", _fake_exchange)
    monkeypatch.setattr("app.routers.auth.entra_oidc.validate_id_token", _fake_validate)


async def test_callback_first_user_becomes_org_admin(api, monkeypatch):
    client, owner_factory = api
    from app.models.organization import Organization
    from app.models.enums import OrganizationStatus
    import uuid

    tenant_id = str(uuid.uuid4())
    async with owner_factory() as db:
        org = Organization(name="Fresh Org", entra_tenant_id=tenant_id, status=OrganizationStatus.active)
        db.add(org)
        await db.commit()

    await _mock_entra_success(monkeypatch, tenant_id=tenant_id, object_id="first-object-id", email="first@example.com")
    client.cookies.set("oauth_state", "matching-state")
    client.cookies.set("oauth_verifier", "some-verifier")

    response = await client.get(
        "/api/auth/callback",
        params={"code": "auth-code", "state": "matching-state"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "dmarc_session" in response.cookies

    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["role"] == "org_admin"


async def test_callback_creates_returning_org_user_as_member(api, monkeypatch):
    client, owner_factory = api
    org, _existing_user = await seed_org_and_user(owner_factory, entra=True)
    tenant_id = str(org.entra_tenant_id)
    await _mock_entra_success(monkeypatch, tenant_id=tenant_id, object_id="new-object-id", email="newperson@example.com")

    client.cookies.set("oauth_state", "matching-state")
    client.cookies.set("oauth_verifier", "some-verifier")

    response = await client.get(
        "/api/auth/callback",
        params={"code": "auth-code", "state": "matching-state"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert "dmarc_session" in response.cookies

    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["role"] == "member"


async def test_callback_organization_not_provisioned(api, monkeypatch):
    client, _owner_factory = api
    await _mock_entra_success(monkeypatch, tenant_id="00000000-0000-0000-0000-000000000000", object_id="oid", email="a@example.com")
    client.cookies.set("oauth_state", "matching-state")
    client.cookies.set("oauth_verifier", "verifier")

    response = await client.get(
        "/api/auth/callback",
        params={"code": "auth-code", "state": "matching-state"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "organization_not_provisioned" in response.headers["location"]


async def test_callback_invalid_state(api):
    client, _owner_factory = api
    client.cookies.set("oauth_state", "cookie-state")
    client.cookies.set("oauth_verifier", "verifier")

    response = await client.get(
        "/api/auth/callback",
        params={"code": "auth-code", "state": "different-state"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "invalid_state" in response.headers["location"]


async def test_callback_entra_error_param(api):
    client, _owner_factory = api
    response = await client.get("/api/auth/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert response.status_code == 302
    assert "access_denied" in response.headers["location"]
