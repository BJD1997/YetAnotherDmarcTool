"""Fixes from the second whole-repo review: demo credential lock-down, the
per-account MFA code limit, audit entries for credential changes, platform
admin resets, self-disable, disabled SSO users, 409s instead of 500s, and
hosted-address DNS record lifecycle."""

import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.models.domain import Domain
from app.models.enums import AuthMethod, DomainVerificationStatus, UserRole, UserStatus
from app.models.user import User
from app.services.auth import entra_oidc
from app.services.auth.password import hash_password
from app.services.auth.rate_limit import otp_account_limiter, otp_limiter
from app.services.cloudflare import dns_provisioner
from app.services.crypto import secrets as crypto_secrets
from tests.conftest import login_as, login_as_platform_admin, seed_org_and_user

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _fernet_key_for_totp_encryption(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


async def _seed_user(owner_factory, org, *, email="teammate@test.example", enrolled=True, **overrides):
    secret = pyotp.random_base32()
    async with owner_factory() as db:
        user = User(
            organization_id=org.id, email=email, role=UserRole.member, status=UserStatus.active,
            auth_method=AuthMethod.local, password_hash=hash_password(PASSWORD),
            otp_secret=secret if enrolled else None,
            otp_enrolled_at=datetime.now(timezone.utc) if enrolled else None,
            **overrides,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        await db.commit()
    return user, secret


# ---------- 1. demo credentials ----------


async def test_demo_account_credentials_cannot_be_changed(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, is_demo_read_only=True)
    await login_as(client, owner_factory, user)

    change = await client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "x" * 20})
    assert change.status_code == 403
    assert (await client.post("/api/auth/mfa/reset/start", json={"current_password": PASSWORD})).status_code == 403
    assert (await client.get("/api/organizations/current")).json()["is_demo_read_only"] is True
    # Signing out still works for demo visitors.
    assert (await client.post("/api/auth/logout")).status_code == 204


# ---------- 2. per-account MFA code limit ----------


async def test_code_guessing_is_capped_per_account_not_just_per_ip(api, monkeypatch):
    client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)
    user, secret = await _seed_user(owner_factory, org)
    # Simulate the attacker spreading guesses over many IPs: the per-IP
    # limiter never fires, only the per-account one can.
    monkeypatch.setattr(otp_limiter._memory, "max_events", 10_000)

    await client.post("/api/auth/local-login", json={"email": user.email, "password": PASSWORD})
    for _ in range(otp_account_limiter.max_events):
        assert (await client.post("/api/auth/verify-otp", json={"code": "000000"})).status_code == 401

    blocked = await client.post("/api/auth/verify-otp", json={"code": pyotp.TOTP(secret).now()})
    assert blocked.status_code == 429

    # The pending login was ended too: once the window passes, the password
    # has to be entered again — the old challenge is gone.
    otp_account_limiter._memory._hits.clear()
    assert (await client.post("/api/auth/verify-otp", json={"code": pyotp.TOTP(secret).now()})).status_code == 401


# ---------- 3. audit trail ----------


async def test_credential_changes_show_up_in_sign_in_activity(api):
    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    user, _ = await _seed_user(owner_factory, org)
    await login_as(client, owner_factory, admin)

    await client.post(f"/api/users/{user.id}/reset-password")
    await client.post(f"/api/users/{user.id}/reset-mfa")

    events = (await client.get("/api/sign-in-events?result=account_change")).json()["events"]
    assert {(e["email"], e["failure_reason"], e["actor_email"]) for e in events} == {
        (user.email, "password_reset_by_admin", admin.email),
        (user.email, "mfa_reset_by_admin", admin.email),
    }


# ---------- 5. platform admin resets ----------


async def test_platform_admin_can_reset_a_locked_out_org_admin(api):
    client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory, entra=True)  # just to have a second org around
    local_org, lone_admin = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        admin_row = await db.get(User, lone_admin.id)
        admin_row.password_hash = hash_password(PASSWORD)
        admin_row.otp_secret = pyotp.random_base32()
        admin_row.otp_enrolled_at = datetime.now(timezone.utc)
        await db.commit()
    await login_as_platform_admin(client, owner_factory)

    users = (await client.get(f"/api/admin/organizations/{local_org.id}/users")).json()
    assert [(u["email"], u["mfa_enrolled"]) for u in users] == [(lone_admin.email, True)]

    reset = await client.post(f"/api/admin/organizations/{local_org.id}/users/{lone_admin.id}/reset-mfa")
    assert reset.status_code == 204
    link = (await client.post(f"/api/admin/organizations/{local_org.id}/users/{lone_admin.id}/reset-password")).json()["setup_link"]
    token = parse_qs(urlparse(link).query)["token"][0]

    client.cookies.clear()
    set_pw = await client.post("/api/auth/set-password", json={"token": token, "new_password": "a brand new password"})
    assert set_pw.json() == {"needs_enrollment": True}

    # Wrong org → 404; SSO users can't be reset here.
    await login_as_platform_admin(client, owner_factory)
    assert (await client.post(f"/api/admin/organizations/{org.id}/users/{lone_admin.id}/reset-mfa")).status_code == 404


# ---------- 6. self-disable ----------


async def test_admin_cannot_disable_themselves(api):
    client, owner_factory = api
    _org, admin = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, admin)

    assert (await client.patch(f"/api/users/{admin.id}", json={"status": "disabled"})).status_code == 400


# ---------- 7/8. Microsoft sign-in edge cases ----------


def _mock_entra(monkeypatch, *, tenant_id: str, object_id: str, exchange_error: bool = False):
    async def _exchange(**_kwargs):
        if exchange_error:
            raise httpx.HTTPStatusError("400 invalid_grant", request=httpx.Request("POST", "https://x"), response=httpx.Response(400))
        return {"id_token": "fake"}

    async def _validate(_token):
        return {"tid": tenant_id, "oid": object_id, "preferred_username": "sso@test.example", "name": "SSO User"}

    monkeypatch.setattr(entra_oidc, "exchange_code_for_tokens", _exchange)
    monkeypatch.setattr(entra_oidc, "validate_id_token", _validate)


async def _callback(client):
    client.cookies.set("oauth_state", "state123")
    client.cookies.set("oauth_verifier", "verifier123")
    return await client.get("/api/auth/callback?code=abc&state=state123", follow_redirects=False)


async def test_disabled_sso_user_gets_a_clear_error_and_no_session(api, monkeypatch):
    client, owner_factory = api
    org, sso_user = await seed_org_and_user(owner_factory, entra=True)
    async with owner_factory() as db:
        row = await db.get(User, sso_user.id)
        row.status = UserStatus.disabled
        row.entra_object_id = "oid-1"
        await db.commit()
    _mock_entra(monkeypatch, tenant_id=str(org.entra_tenant_id), object_id="oid-1")

    response = await _callback(client)

    assert response.headers["location"] == "/login?error=user_disabled"
    assert settings.session_cookie_name not in response.cookies


async def test_rejected_code_exchange_redirects_instead_of_500(api, monkeypatch):
    client, _owner_factory = api
    _mock_entra(monkeypatch, tenant_id=str(uuid.uuid4()), object_id="x", exchange_error=True)

    response = await _callback(client)

    assert response.headers["location"] == "/login?error=token_invalid"


async def test_duplicate_tenant_id_is_a_409(api):
    client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory, entra=True)
    await login_as_platform_admin(client, owner_factory)

    create = await client.post("/api/admin/organizations", json={"name": "Dup", "entra_tenant_id": str(org.entra_tenant_id)})
    assert create.status_code == 409
    other = (await client.post("/api/admin/organizations", json={"name": "Other"})).json()
    patch = await client.patch(f"/api/admin/organizations/{other['id']}", json={"entra_tenant_id": str(org.entra_tenant_id)})
    assert patch.status_code == 409


# ---------- 4. hosted-address DNS records ----------


async def _seed_domain(owner_factory, org, name, *, verified=True, hosted=False):
    async with owner_factory() as db:
        domain = Domain(
            organization_id=org.id, name=name,
            verification_status=DomainVerificationStatus.verified if verified else DomainVerificationStatus.pending,
            hosted_report_address=f"reports+{uuid.uuid4().hex[:12]}@hosted.example" if hosted else None,
        )
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
    return domain


async def test_hosted_address_requires_a_verified_domain(api, monkeypatch):
    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    domain = await _seed_domain(owner_factory, org, "unverified.example", verified=False)
    await login_as(client, owner_factory, admin)

    response = await client.post(f"/api/domains/{domain.id}/hosted-report-address")
    assert response.status_code == 409


async def test_deleting_the_last_hosted_domain_removes_its_dns_record(api, monkeypatch):
    client, owner_factory = api
    removed: list[str] = []

    async def _remove(name):
        removed.append(name)
        return dns_provisioner.ProvisionResult(status="removed", detail=None)

    monkeypatch.setattr(dns_provisioner, "remove_authorization_record", _remove)
    org, admin = await seed_org_and_user(owner_factory)
    other_org, _ = await seed_org_and_user(owner_factory, entra=True)
    shared = await _seed_domain(owner_factory, org, "shared.example", hosted=True)
    await _seed_domain(owner_factory, other_org, "shared.example", hosted=True)
    solo = await _seed_domain(owner_factory, org, "solo.example", hosted=True)
    await login_as(client, owner_factory, admin)

    assert (await client.delete(f"/api/domains/{shared.id}")).status_code == 204
    assert removed == []  # another org still uses shared.example's record
    assert (await client.delete(f"/api/domains/{solo.id}")).status_code == 204
    assert removed == ["solo.example"]

    # Deleting a whole org releases its domains' records too.
    await login_as_platform_admin(client, owner_factory)
    assert (await client.delete(f"/api/admin/organizations/{other_org.id}")).status_code == 204
    assert removed == ["solo.example", "shared.example"]
