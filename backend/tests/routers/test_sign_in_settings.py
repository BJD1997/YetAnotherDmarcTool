"""Settings → Sign-in: only the break-glass account chooses the provider,
and only after a test sign-in with exactly those settings passed; for
Microsoft and Google the tenant or Workspace comes from the provider's
token. The platform admin sets any provider, one at a time."""

import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.models.domain import Domain
from app.models.enums import AuthMethod, DomainVerificationStatus, UserRole, UserStatus
from app.models.organization import Organization
from app.models.session import UserSession
from app.models.user import User
from app.services.auth import entra_oidc, oidc
from app.services.crypto import secrets as crypto_secrets
from tests.conftest import login_as, login_as_platform_admin, seed_org_and_user


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


@pytest.fixture
def fake_provider(monkeypatch):
    """OIDC/Google answers with `claims`; Microsoft with `ms_claims`."""
    state = {
        "claims": {"sub": "kc-1", "email": "jan@acme.example", "email_verified": True},
        "ms_claims": {"tid": str(uuid.uuid4()), "oid": "o1", "preferred_username": "jan@acme.example"},
    }

    async def _discover(provider):
        return {"issuer": provider.issuer, "authorization_endpoint": f"{provider.issuer}/auth", "token_endpoint": "t", "jwks_uri": "j"}

    async def _exchange(doc, provider, **kwargs):
        return {"id_token": "x"}

    async def _validate(doc, provider, id_token, *, nonce):
        state["provider"] = provider
        return state["claims"]

    async def _ms_exchange(**kwargs):
        return {"id_token": "x"}

    async def _ms_validate(id_token):
        # A real token carries the nonce this sign-in sent.
        return {**state["ms_claims"], "nonce": state.get("nonce", "")}

    monkeypatch.setattr(oidc, "discover", _discover)
    monkeypatch.setattr(oidc, "exchange_code", _exchange)
    monkeypatch.setattr(oidc, "validate_id_token", _validate)
    monkeypatch.setattr(entra_oidc, "exchange_code_for_tokens", _ms_exchange)
    monkeypatch.setattr(entra_oidc, "validate_id_token", _ms_validate)
    monkeypatch.setattr(settings, "entra_sso_client_id", "ms-client")
    monkeypatch.setattr(settings, "google_sso_client_id", "g-client")
    monkeypatch.setattr(settings, "google_sso_client_secret", "g-secret")
    return state


OIDC = {"provider": "oidc", "issuer": "https://idp.acme.example", "client_id": "yadt", "client_secret": "s3cret"}


async def _break_glass(client, owner_factory, **org_fields):
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, break_glass=True, **org_fields)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="acme.example", verification_status=DomainVerificationStatus.verified))
        await db.commit()
    await login_as(client, owner_factory, user)
    return org, user


async def _run_test(client, body, callback: str, fake: dict | None = None) -> dict:
    started = await client.post("/api/organizations/current/sign-in/test", json=body)
    assert started.status_code == 200, started.text
    redirect = await client.get(started.json()["start_url"], follow_redirects=False)
    assert redirect.status_code == 302, redirect.text
    # The provider answers; mode and org ride in the cookies set above.
    state = redirect.cookies.get("oauth_state") or client.cookies.get("oauth_state")
    if fake is not None:
        fake["nonce"] = redirect.cookies.get("oauth_nonce") or client.cookies.get("oauth_nonce")
    done = await client.get(callback, params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code == 200
    import json

    payload, _end = json.JSONDecoder().raw_decode(done.text, done.text.index("postMessage(") + len("postMessage("))
    return payload


async def test_the_break_glass_saves_oidc_after_a_passed_test(api, fake_provider):
    client, owner_factory = api
    await _break_glass(client, owner_factory)

    refused = await client.put("/api/organizations/current/sign-in", json={**OIDC, "ticket": "nope"})
    assert refused.status_code == 400

    result = await _run_test(client, OIDC, "/api/auth/oidc/callback")
    assert result["ok"] is True, result

    saved = await client.put("/api/organizations/current/sign-in", json={**OIDC, "ticket": result["ticket"]})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["provider"] == "oidc" and out["oidc"]["secret_set"] is True and out["can_change"] is True
    assert "s3cret" not in saved.text


async def test_microsoft_takes_the_tenant_from_the_token(api, fake_provider):
    client, owner_factory = api
    org, _ = await _break_glass(client, owner_factory)
    result = await _run_test(client, {"provider": "microsoft"}, "/api/auth/callback", fake_provider)
    assert result["ok"] is True and result["tenant_id"] == fake_provider["ms_claims"]["tid"]

    saved = await client.put("/api/organizations/current/sign-in", json={"provider": "microsoft", "ticket": result["ticket"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["provider"] == "microsoft" and saved.json()["entra_tenant_id"] == fake_provider["ms_claims"]["tid"]


async def test_google_takes_the_workspace_from_the_token(api, fake_provider):
    client, owner_factory = api
    await _break_glass(client, owner_factory)
    fake_provider["claims"] = {"sub": "g-1", "email": "jan@acme.example", "email_verified": True, "hd": "acme.example"}
    result = await _run_test(client, {"provider": "google"}, "/api/auth/google/callback")
    assert result["ok"] is True and result["workspace"] == "acme.example"
    saved = await client.put("/api/organizations/current/sign-in", json={"provider": "google", "ticket": result["ticket"]})
    assert saved.json()["provider"] == "google" and saved.json()["google_workspace_domain"] == "acme.example"


async def test_another_orgs_tenant_cant_be_claimed(api, fake_provider):
    client, owner_factory = api
    taken = uuid.uuid4()
    async with owner_factory() as db:
        db.add(Organization(name="Owner", entra_tenant_id=taken))
        await db.commit()
    await _break_glass(client, owner_factory)
    fake_provider["ms_claims"]["tid"] = str(taken)
    result = await _run_test(client, {"provider": "microsoft"}, "/api/auth/callback", fake_provider)
    assert result["ok"] is False and "another organization" in result["error"]


async def test_the_test_account_must_be_on_a_verified_domain(api, fake_provider):
    client, owner_factory = api
    await _break_glass(client, owner_factory)
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@elsewhere.example", "email_verified": True}
    result = await _run_test(client, OIDC, "/api/auth/oidc/callback")
    assert result["ok"] is False and "verified domains" in result["error"]


async def test_a_ticket_is_only_good_for_the_settings_it_tested(api, fake_provider):
    client, owner_factory = api
    await _break_glass(client, owner_factory)
    ticket = (await _run_test(client, OIDC, "/api/auth/oidc/callback"))["ticket"]
    other = {**OIDC, "issuer": "https://idp.evil.example", "ticket": ticket}
    assert (await client.put("/api/organizations/current/sign-in", json=other)).status_code == 400
    as_google = {"provider": "google", "ticket": ticket}
    assert (await client.put("/api/organizations/current/sign-in", json=as_google)).status_code == 400


async def test_only_the_break_glass_account_can_change_it(api, fake_provider):
    client, owner_factory = api
    _org, admin = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, admin)
    assert (await client.post("/api/organizations/current/sign-in/test", json=OIDC)).status_code == 403
    assert (await client.delete("/api/organizations/current/sign-in")).status_code == 403
    assert (await client.get("/api/organizations/current/sign-in")).json()["can_change"] is False


async def test_switching_ends_provider_sessions_and_other_local_accounts(api, fake_provider):
    client, owner_factory = api
    org, keys = await _break_glass(client, owner_factory)
    async with owner_factory() as db:
        local_mate = User(organization_id=org.id, email="mate@acme.example", role=UserRole.member, auth_method=AuthMethod.local)
        old_sso = User(organization_id=org.id, email="old@acme.example", role=UserRole.member, auth_method=AuthMethod.entra, entra_object_id="x")
        db.add_all([local_mate, old_sso])
        await db.commit()
    from datetime import datetime, timedelta, timezone

    from app.services.auth import session_manager

    async with owner_factory() as db:
        await session_manager.create_user_session(db, user_id=old_sso.id, organization_id=org.id, ip_address=None, user_agent=None)
        await db.commit()

    result = await _run_test(client, OIDC, "/api/auth/oidc/callback")
    assert (await client.put("/api/organizations/current/sign-in", json={**OIDC, "ticket": result["ticket"]})).status_code == 200

    async with owner_factory() as db:
        assert (await db.get(User, local_mate.id)).status == UserStatus.disabled
        assert (await db.get(User, keys.id)).status == UserStatus.active
        sessions = (await db.execute(select(UserSession).where(UserSession.user_id == old_sso.id))).scalars().all()
        assert sessions and all(s.revoked_at is not None for s in sessions)
    # The break-glass account is still signed in.
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_back_to_local_accounts_only(api, fake_provider):
    client, owner_factory = api
    await _break_glass(client, owner_factory, entra=True)
    removed = await client.delete("/api/organizations/current/sign-in")
    assert removed.status_code == 200 and removed.json()["provider"] == "none"


async def test_platform_admin_sets_a_provider_and_only_one(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    created = await client.post("/api/admin/organizations", json={"name": "Acme B.V.", "google_workspace_domain": "Acme.example"})
    assert created.status_code == 201, created.text
    org = created.json()
    assert (org["sign_in_provider"], org["google_workspace_domain"]) == ("google", "acme.example")
    assert len(org["slug"]) == 10 and "acme" not in org["slug"]

    both = await client.patch(
        f"/api/admin/organizations/{org['id']}",
        json={"oidc_issuer": "https://idp.acme.example", "oidc_client_id": "c", "oidc_client_secret": "s"},
    )
    assert both.status_code == 409

    switched = await client.patch(
        f"/api/admin/organizations/{org['id']}",
        json={"google_workspace_domain": None, "oidc_issuer": "https://idp.acme.example", "oidc_client_id": "c", "oidc_client_secret": "s"},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["sign_in_provider"] == "oidc" and switched.json()["oidc_secret_set"] is True


async def test_platform_admin_slugs_are_random_unless_chosen_and_unique(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    first = (await client.post("/api/admin/organizations", json={"name": "Acme"})).json()
    second = (await client.post("/api/admin/organizations", json={"name": "Acme"})).json()
    assert first["slug"] != second["slug"]
    assert (await client.patch(f"/api/admin/organizations/{first['id']}", json={"slug": "acme"})).json()["slug"] == "acme"
    taken = await client.patch(f"/api/admin/organizations/{second['id']}", json={"slug": "acme"})
    assert taken.status_code == 409
