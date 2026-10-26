"""Google and per-organization OIDC sign-in, with the provider faked at the
oidc module boundary (its own checks are tested in test_oidc.py)."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import settings
from app.services.crypto import secrets as crypto_secrets

from app.models.domain import Domain
from app.models.enums import AuthMethod, DomainVerificationStatus, OrganizationStatus
from app.models.organization import Organization
from app.models.user import User
from app.services.auth import oidc


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    """OIDC client secrets are encrypted at rest, like two-factor secrets."""
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()
    yield
    crypto_secrets._fernet.cache_clear()


@pytest.fixture
def fake_provider(monkeypatch):
    """Whatever claims the test puts in `claims` come back as a valid token."""
    state = {"claims": {}}

    async def _discover(provider):
        return {"issuer": provider.issuer, "authorization_endpoint": f"{provider.issuer}/auth", "token_endpoint": "t", "jwks_uri": "j"}

    async def _exchange(doc, provider, **kwargs):
        state["provider"] = provider
        return {"id_token": "x"}

    async def _validate(doc, provider, id_token, *, nonce):
        if nonce != "test-nonce":
            raise oidc.OidcError("nonce")
        return state["claims"]

    monkeypatch.setattr(oidc, "discover", _discover)
    monkeypatch.setattr(oidc, "exchange_code", _exchange)
    monkeypatch.setattr(oidc, "validate_id_token", _validate)
    return state


@pytest.fixture
def google_app(monkeypatch):
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_id", "g-client")
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_secret", "g-secret")


def _set_flow_cookies(client, org_id: str | None = None, mode: str = "sign_in"):
    client.cookies.clear()
    client.cookies.set("oauth_state", "matching-state")
    client.cookies.set("oauth_verifier", "v")
    client.cookies.set("oauth_nonce", "test-nonce")
    client.cookies.set("oauth_mode", mode)
    if org_id:
        client.cookies.set("oauth_org", org_id)


async def _org(owner_factory, **fields) -> Organization:
    async with owner_factory() as db:
        org = Organization(name=fields.pop("name", "Acme"), status=OrganizationStatus.active, **fields)
        db.add(org)
        await db.flush()
        await db.refresh(org)
        await db.commit()
        return org


async def _verified_domain(owner_factory, org, name: str):
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name=name, verification_status=DomainVerificationStatus.verified))
        await db.commit()


async def _callback(client, provider: str):
    return await client.get(
        f"/api/auth/{provider}/callback", params={"code": "c", "state": "matching-state"}, follow_redirects=False
    )


# --- Google ------------------------------------------------------------------

async def test_google_signs_in_a_workspace_user(api, fake_provider, google_app):
    client, owner_factory = api
    org = await _org(owner_factory, google_workspace_domain="acme.example", expected_admin_email="boss@acme.example")
    fake_provider["claims"] = {"sub": "g-1", "email": "Boss@acme.example", "email_verified": True, "hd": "acme.example", "name": "Boss"}

    _set_flow_cookies(client)
    response = await _callback(client, "google")

    assert response.status_code == 302 and response.headers["location"] == "/"
    assert (await client.get("/api/auth/me")).json()["role"] == "org_admin"
    async with owner_factory() as db:
        user = (await db.execute(select(User).where(User.organization_id == org.id))).scalar_one()
        assert (user.auth_method, user.external_subject) == (AuthMethod.google, "g-1")


async def test_google_signs_the_same_person_in_again_as_the_same_user(api, fake_provider, google_app):
    client, owner_factory = api
    org = await _org(owner_factory, google_workspace_domain="acme.example")
    fake_provider["claims"] = {"sub": "g-1", "email": "jan@acme.example", "email_verified": True, "hd": "acme.example"}
    for _ in range(2):
        _set_flow_cookies(client)
        assert (await _callback(client, "google")).headers["location"] == "/"
    async with owner_factory() as db:
        assert len((await db.execute(select(User).where(User.organization_id == org.id))).scalars().all()) == 1


@pytest.mark.parametrize(
    "claims,reason",
    [
        ({"sub": "g-1", "email": "a@gmail.com", "email_verified": True}, "hd_mismatch"),
        ({"sub": "g-1", "email": "a@other.example", "email_verified": True, "hd": "other.example"}, "organization_not_provisioned"),
        ({"sub": "g-1", "email": "a@acme.example", "email_verified": False, "hd": "acme.example"}, "email_not_verified"),
    ],
)
async def test_google_refuses_accounts_outside_a_linked_workspace(api, fake_provider, google_app, claims, reason):
    client, owner_factory = api
    await _org(owner_factory, google_workspace_domain="acme.example")
    fake_provider["claims"] = claims

    _set_flow_cookies(client)
    assert (await _callback(client, "google")).headers["location"] == f"/login?error={reason}"


async def test_google_login_is_404_without_the_app(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_id", None)
    assert (await client.get("/api/auth/google/login", follow_redirects=False)).status_code == 404


async def test_google_login_asks_for_workspace_accounts(api, fake_provider, google_app):
    client, _ = api
    response = await client.get("/api/auth/google/login", params={"login_hint": "jan@acme.example"}, follow_redirects=False)
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/auth?") and "hd=%2A" in location and "login_hint=" in location


async def test_a_mismatched_state_is_refused(api, fake_provider, google_app):
    client, _ = api
    _set_flow_cookies(client)
    response = await client.get("/api/auth/google/callback", params={"code": "c", "state": "other"}, follow_redirects=False)
    assert response.headers["location"] == "/login?error=invalid_state"


# --- an organization's own provider ------------------------------------------

async def _oidc_org(owner_factory, **fields):
    org = await _org(
        owner_factory, slug="acme", oidc_issuer="https://idp.acme.example", oidc_client_id="yadt", oidc_client_secret="s",
        **fields,
    )
    await _verified_domain(owner_factory, org, "acme.example")
    return org


async def test_oidc_start_redirects_to_the_orgs_provider(api, fake_provider):
    client, owner_factory = api
    org = await _oidc_org(owner_factory)
    response = await client.get("/api/auth/oidc/start", params={"org": "acme", "login_hint": "jan@acme.example"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://idp.acme.example/auth?")
    assert response.cookies.get("oauth_org") == str(org.id)


async def test_oidc_signs_in_with_an_email_on_a_verified_domain(api, fake_provider):
    client, owner_factory = api
    org = await _oidc_org(owner_factory)
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@mail.acme.example", "email_verified": True, "name": "Jan"}

    _set_flow_cookies(client, str(org.id))
    response = await _callback(client, "oidc")

    assert response.headers["location"] == "/"
    assert fake_provider["provider"].issuer == "https://idp.acme.example"
    assert (await client.get("/api/auth/me")).json()["role"] == "member"


async def test_oidc_refuses_an_email_outside_the_orgs_verified_domains(api, fake_provider):
    client, owner_factory = api
    org = await _oidc_org(owner_factory)
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@evil.example", "email_verified": True}

    _set_flow_cookies(client, str(org.id))
    assert (await _callback(client, "oidc")).headers["location"] == "/login?error=email_domain_not_verified"


async def test_oidc_checks_against_the_org_in_the_cookie_not_the_response(api, fake_provider):
    # Another org's provider can't sign anyone into this one: the token is
    # checked with the settings of the org the cookie names.
    client, owner_factory = api
    acme = await _oidc_org(owner_factory)
    other = await _org(
        owner_factory, name="Other", slug="other", oidc_issuer="https://idp.other.example", oidc_client_id="o", oidc_client_secret="s"
    )
    await _verified_domain(owner_factory, other, "other.example")
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@other.example", "email_verified": True}

    _set_flow_cookies(client, str(acme.id))
    response = await _callback(client, "oidc")
    assert fake_provider["provider"].issuer == "https://idp.acme.example"
    assert response.headers["location"] == "/login?error=email_domain_not_verified"


async def test_oidc_start_for_an_unknown_org_goes_back_to_login(api, fake_provider):
    client, _ = api
    response = await client.get("/api/auth/oidc/start", params={"org": "nope"}, follow_redirects=False)
    assert response.headers["location"] == "/login?error=organization_not_provisioned"


async def test_a_suspended_org_cant_sign_in(api, fake_provider):
    client, owner_factory = api
    org = await _oidc_org(owner_factory)
    async with owner_factory() as db:
        o = await db.get(Organization, org.id)
        o.status = OrganizationStatus.suspended
        await db.commit()
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@acme.example", "email_verified": True}
    _set_flow_cookies(client, str(org.id))
    assert (await _callback(client, "oidc")).headers["location"] == "/login?error=organization_suspended"


# --- the login page: routing an email, direct links --------------------------

async def test_discover_routes_an_email_on_a_verified_domain(api, fake_provider):
    client, owner_factory = api
    await _oidc_org(owner_factory)
    response = await client.post("/api/auth/discover", json={"email": "Jan@Mail.Acme.example"})
    assert response.json() == {"url": "/api/auth/oidc/start?org=acme&login_hint=Jan%40Mail.Acme.example"}


@pytest.mark.parametrize("email", ["jan@unknown.example", "jan@gmail.com", "not-an-email"])
async def test_discover_gives_nothing_away_for_other_addresses(api, email):
    client, owner_factory = api
    await _oidc_org(owner_factory)
    response = await client.post("/api/auth/discover", json={"email": email})
    assert response.status_code == 200 and response.json() == {"url": None}


async def test_discover_ignores_unverified_domains(api):
    client, owner_factory = api
    org = await _org(owner_factory, slug="sneaky", oidc_issuer="https://idp.sneaky.example", oidc_client_id="c", oidc_client_secret="s")
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="victim.example", verification_status=DomainVerificationStatus.pending))
        await db.commit()
    assert (await client.post("/api/auth/discover", json={"email": "ceo@victim.example"})).json() == {"url": None}


async def test_discover_sends_microsoft_and_google_orgs_to_their_button(api, monkeypatch, google_app):
    import uuid as _uuid

    client, owner_factory = api
    monkeypatch.setattr("app.routers.auth_providers.settings.entra_sso_client_id", "ms")
    ms = await _org(owner_factory, name="MS", slug="ms", entra_tenant_id=_uuid.uuid4())
    await _verified_domain(owner_factory, ms, "ms.example")
    g = await _org(owner_factory, name="G", slug="g", google_workspace_domain="g.example")
    await _verified_domain(owner_factory, g, "g.example")

    assert (await client.post("/api/auth/discover", json={"email": "a@ms.example"})).json()["url"].startswith("/api/auth/login?login_hint=")
    assert (await client.post("/api/auth/discover", json={"email": "a@g.example"})).json()["url"].startswith("/api/auth/google/login?login_hint=")


async def test_the_direct_link_finds_the_orgs_provider(api):
    client, owner_factory = api
    await _oidc_org(owner_factory)
    assert (await client.get("/api/auth/org/acme")).json() == {"url": "/api/auth/oidc/start?org=acme"}
    assert (await client.get("/api/auth/org/nope")).status_code == 404


async def test_config_says_whether_company_sign_in_exists(api):
    client, owner_factory = api
    assert (await client.get("/api/auth/config")).json()["company_sign_in_enabled"] is False
    await _oidc_org(owner_factory)
    assert (await client.get("/api/auth/config")).json()["company_sign_in_enabled"] is True


async def test_link_lookups_are_rate_limited(api):
    client, _ = api
    statuses = [(await client.get("/api/auth/org/nope")).status_code for _ in range(61)]
    assert statuses[:60] == [404] * 60 and statuses[60] == 429
