"""Settings → Sign-in: an org admin's own OIDC provider is only saved after
a test sign-in with exactly those settings passed; the platform admin sets
any provider, one at a time."""

import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus, UserRole
from app.services.auth import oidc
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
    state = {"claims": {"sub": "kc-1", "email": "jan@acme.example", "email_verified": True}}

    async def _discover(provider):
        return {"issuer": provider.issuer, "authorization_endpoint": f"{provider.issuer}/auth", "token_endpoint": "t", "jwks_uri": "j"}

    async def _exchange(doc, provider, **kwargs):
        return {"id_token": "x"}

    async def _validate(doc, provider, id_token, *, nonce):
        state["provider"] = provider
        return state["claims"]

    monkeypatch.setattr(oidc, "discover", _discover)
    monkeypatch.setattr(oidc, "exchange_code", _exchange)
    monkeypatch.setattr(oidc, "validate_id_token", _validate)
    return state


SETTINGS = {"issuer": "https://idp.acme.example", "client_id": "yadt", "client_secret": "s3cret"}


async def _admin(client, owner_factory, **org_fields):
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, **org_fields)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="acme.example", verification_status=DomainVerificationStatus.verified))
        await db.commit()
    await login_as(client, owner_factory, user)
    return org


async def _run_test(client, body=SETTINGS) -> str:
    started = await client.post("/api/organizations/current/sign-in/oidc/test", json=body)
    assert started.status_code == 200, started.text
    redirect = await client.get(started.json()["start_url"], follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"].startswith("https://idp.acme.example/auth")
    state = redirect.cookies["oauth_state"]
    done = await client.get("/api/auth/oidc/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code == 200
    return done.text


def _ticket(page: str) -> str:
    return page.split('"ticket": "')[1].split('"')[0]


async def test_saving_needs_a_passed_test(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory)

    refused = await client.put("/api/organizations/current/sign-in/oidc", json={**SETTINGS, "ticket": "nope"})
    assert refused.status_code == 400

    page = await _run_test(client)
    assert '"ok": true' in page

    saved = await client.put("/api/organizations/current/sign-in/oidc", json={**SETTINGS, "ticket": _ticket(page)})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["provider"] == "oidc"
    assert out["oidc"] == {"issuer": "https://idp.acme.example", "client_id": "yadt", "secret_set": True}
    assert "s3cret" not in saved.text
    assert (await client.get("/api/organizations/current")).json()["sign_in_provider"] == "oidc"


async def test_a_ticket_is_only_good_for_the_settings_it_tested(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory)
    ticket = _ticket(await _run_test(client))
    other = {**SETTINGS, "issuer": "https://idp.evil.example", "ticket": ticket}
    assert (await client.put("/api/organizations/current/sign-in/oidc", json=other)).status_code == 400


async def test_a_failed_test_reports_why(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory)
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@elsewhere.example", "email_verified": True}
    page = await _run_test(client)
    assert '"ok": false' in page and "verified domains" in page


async def test_the_stored_secret_is_only_reused_for_the_same_provider(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory)
    page = await _run_test(client)
    await client.put("/api/organizations/current/sign-in/oidc", json={**SETTINGS, "ticket": _ticket(page)})

    same = {"issuer": SETTINGS["issuer"], "client_id": "yadt"}
    assert (await client.post("/api/organizations/current/sign-in/oidc/test", json=same)).status_code == 200
    moved = {"issuer": "https://idp.other.example", "client_id": "yadt"}
    response = await client.post("/api/organizations/current/sign-in/oidc/test", json=moved)
    assert response.status_code == 400 and "client secret" in response.text


async def test_members_cant_change_sign_in(api, fake_provider):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)
    assert (await client.post("/api/organizations/current/sign-in/oidc/test", json=SETTINGS)).status_code == 403


async def test_a_microsoft_org_cant_add_its_own_provider(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory, entra=True)
    response = await client.post("/api/organizations/current/sign-in/oidc/test", json=SETTINGS)
    assert response.status_code == 409 and "Microsoft" in response.text


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
    assert "\"s\"" not in switched.text


async def test_platform_admin_slugs_are_random_unless_chosen_and_unique(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    first = (await client.post("/api/admin/organizations", json={"name": "Acme"})).json()
    second = (await client.post("/api/admin/organizations", json={"name": "Acme"})).json()
    assert first["slug"] != second["slug"]
    assert (await client.patch(f"/api/admin/organizations/{first['id']}", json={"slug": "acme"})).json()["slug"] == "acme"
    taken = await client.patch(f"/api/admin/organizations/{second['id']}", json={"slug": "acme"})
    assert taken.status_code == 409
