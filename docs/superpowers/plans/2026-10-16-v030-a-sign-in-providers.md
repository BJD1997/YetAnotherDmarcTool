# v0.3.0 part A: sign-in with Google and any OIDC provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organizations can sign in with Google Workspace or their own OpenID Connect provider next to Microsoft and local accounts, and the login page routes a work email address to the right provider.

**Architecture:** One generic OIDC module (discovery, PKCE authorization URL, code exchange, ID-token checks) used by Google and per-organization providers; Microsoft keeps its own token code but shares one "finish the sign-in" step with the others. Provider settings live on `organizations`; users from Google/OIDC are keyed by the provider's `sub` in `users.external_subject`. A stateless, Fernet-encrypted test flow lets an org admin prove new OIDC settings work before saving them.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, PyJWT (already a dependency), httpx, cryptography (Fernet, already used), React + TanStack Query, vitest.

**Spec:** `docs/superpowers/specs/2026-10-16-v030-sign-in-and-report-sources-design.md` (section A)

## Global Constraints

- No new dependencies (backend or frontend).
- Every outgoing request to an organization's issuer goes through `assert_public_host` unless `OIDC_ALLOW_PRIVATE_ISSUERS=true`; HTTPS only unless that same switch is on; no redirects followed; responses capped at 1 MB.
- Tokens are checked against the organization chosen **before** the redirect (cookie), never against anything in the response.
- Email-domain routing and OIDC email checks use **verified** domains only; public mailbox domains (`sender_auth.FREE_MAILBOX_DOMAINS`) never route.
- The login page and `/api/auth/discover` never reveal an organization's name.
- OIDC client secrets never reach the browser; stored with `EncryptedSecret` (FERNET_KEY).
- Local accounts keep working in every organization, next to any provider.
- No mention of SAML anywhere (code, docs, UI).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.claude/` or `qa/`.
- Tests: backend `TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/dmarc_test_wt PYTHONDONTWRITEBYTECODE=1 ~/.local/bin/pytest -q -p no:cacheprovider <paths>` from `backend/`; frontend `npx tsc -b && npx vitest run --maxWorkers=1 <paths>` from `frontend/`. Never run two heavy suites at once.

## Rulings made while planning (deviations from the spec text)

- `sign_in_provider` is **derived** (a model property from which of `entra_tenant_id`, `google_workspace_domain`, `oidc_issuer` is set) instead of a stored column, plus a DB check constraint that at most one is set. Same behaviour, no way for the column and the settings to disagree. Cost if wrong: add the column later.
- **Org admins manage OIDC only** (with the test flow). Google Workspace domain and Microsoft tenant are set by the platform admin, who can save without a test (they are trusted, and local accounts keep working so nobody is locked out). Cost if wrong: an org-admin Google setting later, reusing the test flow.
- A person who has both a local account and a provider account in the same organization has **two separate users** (no linking by email: linking is a classic takeover path). Team shows the method per user.

## File structure

Backend (new):
- `backend/alembic/versions/0043_sign_in_providers.py` — schema.
- `backend/app/services/organizations/slug.py` — `slugify`, `unique_slug`.
- `backend/app/services/auth/oidc.py` — generic OIDC client.
- `backend/app/services/auth/sign_in_flow.py` — shared finish step.
- `backend/app/services/auth/oauth_cookies.py` — state/verifier/nonce/org cookies for Google and OIDC.
- `backend/app/services/auth/oidc_test.py` — encrypted pending-settings cookie and save ticket.
- `backend/app/routers/auth_providers.py` — Google and per-org OIDC routes, discover, org lookup by slug.
- Tests: `backend/tests/services/auth/test_oidc.py`, `backend/tests/routers/test_auth_providers.py`, `backend/tests/routers/test_sign_in_settings.py`, `backend/tests/services/test_slug.py`.

Backend (modified): `app/models/organization.py`, `app/models/user.py`, `app/models/enums.py`, `app/config.py`, `app/repositories/auth.py`, `app/routers/auth.py`, `app/services/auth/entra_oidc.py`, `app/routers/organizations.py`, `app/routers/platform_admin.py`, `app/schemas/platform_admin.py`, `app/routers/users.py`, `app/main.py` (router include), `.env.example`.

Frontend (new): `src/pages/LoginOrg.tsx`, `src/pages/settings/SignInTab.tsx` (+ tests), `src/hooks/useSignInSettings.ts`.
Frontend (modified): `src/pages/Login.tsx`, `src/App.tsx`, `src/pages/SettingsLayout.tsx`, `src/api/types.ts`, `src/pages/admin/AdminOrganizations.tsx`, `src/hooks/useAdmin.ts`, `src/pages/Team.tsx`.

---

### Task 1: Schema — provider settings, slug, external subject

**Files:**
- Create: `backend/alembic/versions/0043_sign_in_providers.py`, `backend/app/services/organizations/slug.py` (and `__init__.py` if the package doesn't exist), `backend/tests/services/test_slug.py`
- Modify: `backend/app/models/organization.py`, `backend/app/models/user.py`, `backend/app/models/enums.py`

**Interfaces:**
- Produces: `Organization.google_workspace_domain: str | None`, `Organization.oidc_issuer: str | None`, `Organization.oidc_client_id: str | None`, `Organization.oidc_client_secret: str | None` (EncryptedSecret), `Organization.slug: str`, property `Organization.sign_in_provider -> Literal["none","microsoft","google","oidc"]`; `User.external_subject: str | None`; `AuthMethod.google`, `AuthMethod.oidc`; `slugify(name: str) -> str`, `unique_slug(name: str, taken: set[str]) -> str`.

- [ ] **Step 1: Write the failing slug test**

```python
# backend/tests/services/test_slug.py
from app.services.organizations.slug import slugify, unique_slug


def test_slugify_keeps_letters_digits_and_single_dashes():
    assert slugify("  Acme B.V. — Mail ") == "acme-b-v-mail"
    assert slugify("Ünïcode Org") == "unicode-org"
    assert slugify("!!!") == "org"
    assert len(slugify("x" * 200)) == 63


def test_unique_slug_adds_a_number_when_taken():
    assert unique_slug("Acme", set()) == "acme"
    assert unique_slug("Acme", {"acme", "acme-2"}) == "acme-3"
```

- [ ] **Step 2: Run it — expect ImportError**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/services/test_slug.py` → FAIL (module not found).

- [ ] **Step 3: Implement slug helpers**

```python
# backend/app/services/organizations/slug.py
"""The short name in an organization's direct sign-in link, /login/<slug>."""

import re
import unicodedata

_NOT_ALLOWED = re.compile(r"[^a-z0-9]+")
MAX_LENGTH = 63


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = _NOT_ALLOWED.sub("-", ascii_name.lower()).strip("-")[:MAX_LENGTH].rstrip("-")
    return slug or "org"


def unique_slug(name: str, taken: set[str]) -> str:
    base = slugify(name)
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: MAX_LENGTH - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1
```

- [ ] **Step 4: Migration**

```python
# backend/alembic/versions/0043_sign_in_providers.py
"""Sign-in with Google Workspace or an organization's own OIDC provider

An organization has local accounts and at most one sign-in provider:
Microsoft (entra_tenant_id, as before), Google Workspace
(google_workspace_domain) or its own OpenID Connect provider (oidc_*).
slug is the short name in its direct sign-in link, /login/<slug>.
Users from Google or OIDC are keyed by the provider's stable user ID (sub)
in external_subject; Microsoft users keep entra_object_id.

Revision ID: 0043_sign_in_providers
Revises: 0042_imap_mailbox
"""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0043_sign_in_providers"
down_revision = "0042_imap_mailbox"
branch_labels = None
depends_on = None


def _slugify(name: str) -> str:
    # A copy of app/services/organizations/slug.py: migrations don't import app code.
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:63].rstrip("-")
    return slug or "org"


def upgrade() -> None:
    op.execute("ALTER TYPE auth_method ADD VALUE IF NOT EXISTS 'google'")
    op.execute("ALTER TYPE auth_method ADD VALUE IF NOT EXISTS 'oidc'")

    op.add_column("organizations", sa.Column("google_workspace_domain", sa.String(253), nullable=True))
    op.create_unique_constraint("uq_organizations_google_workspace_domain", "organizations", ["google_workspace_domain"])
    op.add_column("organizations", sa.Column("oidc_issuer", sa.String(2048), nullable=True))
    op.add_column("organizations", sa.Column("oidc_client_id", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("oidc_client_secret", sa.String(1024), nullable=True))
    op.create_check_constraint(
        "ck_organizations_one_sign_in_provider",
        "organizations",
        "num_nonnulls(entra_tenant_id, google_workspace_domain, oidc_issuer) <= 1",
    )
    op.create_check_constraint(
        "ck_organizations_oidc_complete",
        "organizations",
        "(oidc_issuer IS NULL) = (oidc_client_id IS NULL) AND (oidc_issuer IS NULL) = (oidc_client_secret IS NULL)",
    )

    op.add_column("organizations", sa.Column("slug", sa.String(63), nullable=True))
    bind = op.get_bind()
    taken: set[str] = set()
    for org_id, name in bind.execute(sa.text("SELECT id, name FROM organizations ORDER BY created_at")).all():
        base = _slugify(name)
        slug, n = base, 2
        while slug in taken:
            suffix = f"-{n}"
            slug = base[: 63 - len(suffix)] + suffix
            n += 1
        taken.add(slug)
        bind.execute(sa.text("UPDATE organizations SET slug = :slug WHERE id = :id"), {"slug": slug, "id": org_id})
    op.alter_column("organizations", "slug", nullable=False)
    op.create_unique_constraint("uq_organizations_slug", "organizations", ["slug"])

    op.add_column("users", sa.Column("external_subject", sa.String(255), nullable=True))
    op.create_unique_constraint(
        "uq_users_org_method_external_subject", "users", ["organization_id", "auth_method", "external_subject"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_org_method_external_subject", "users")
    op.drop_column("users", "external_subject")
    op.drop_constraint("uq_organizations_slug", "organizations")
    op.drop_column("organizations", "slug")
    op.drop_constraint("ck_organizations_oidc_complete", "organizations")
    op.drop_constraint("ck_organizations_one_sign_in_provider", "organizations")
    for column in ("oidc_client_secret", "oidc_client_id", "oidc_issuer"):
        op.drop_column("organizations", column)
    op.drop_constraint("uq_organizations_google_workspace_domain", "organizations")
    op.drop_column("organizations", "google_workspace_domain")
    # Postgres can't drop enum values; google/oidc stay in auth_method, unused.
```

- [ ] **Step 5: Models and enum**

`app/models/enums.py`, in `AuthMethod` after `local = "local"`:

```python
    google = "google"
    oidc = "oidc"
```

`app/models/organization.py`: add imports `import secrets`, `from typing import Literal`, `from app.services.auth.totp_secret import EncryptedSecret`, `from app.services.organizations.slug import slugify`, and after `entra_tenant_id`:

```python
    # Google Workspace sign-in: the Workspace's primary domain, matched
    # against the `hd` claim Google puts in the ID token.
    google_workspace_domain: Mapped[str | None] = mapped_column(String(253), unique=True, nullable=True)
    # The organization's own OpenID Connect provider (Authentik, Keycloak,
    # ...). The rest comes from the issuer's discovery document.
    oidc_issuer: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    oidc_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oidc_client_secret: Mapped[str | None] = mapped_column(EncryptedSecret(1024), nullable=True)
    # The short name in its direct sign-in link, /login/<slug>. Code that
    # creates organizations for real sets a clean unique one (unique_slug);
    # the default only keeps other code paths (tests, the demo seed) working.
    slug: Mapped[str] = mapped_column(
        String(63),
        unique=True,
        nullable=False,
        default=lambda ctx: f"{slugify(ctx.get_current_parameters()['name'])[:56]}-{secrets.token_hex(3)}",
    )

    @property
    def sign_in_provider(self) -> Literal["none", "microsoft", "google", "oidc"]:
        if self.entra_tenant_id is not None:
            return "microsoft"
        if self.google_workspace_domain:
            return "google"
        if self.oidc_issuer:
            return "oidc"
        return "none"
```

`app/models/user.py`: add the column and constraint:

```python
    __table_args__ = (
        UniqueConstraint("organization_id", "entra_object_id", name="uq_users_org_entra_object_id"),
        UniqueConstraint("organization_id", "auth_method", "external_subject", name="uq_users_org_method_external_subject"),
    )
    ...
    # Google/OIDC users: the provider's stable user ID (the `sub` claim).
    external_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

(Keep the existing `__table_args__` contents; add the second constraint.)

- [ ] **Step 6: Run slug tests, migrate the test DB, run the RLS and auth suites**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/services/test_slug.py tests/routers/test_auth.py tests/rls` → PASS (the test fixture builds the schema via migrations; if it uses `metadata.create_all`, the model changes cover it).

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0043_sign_in_providers.py backend/app/services/organizations backend/app/models backend/tests/services/test_slug.py
git commit -m "Sign-in providers: schema for Google, OIDC, org slugs and external subjects"
```

---

### Task 2: Generic OIDC client

**Files:**
- Create: `backend/app/services/auth/oidc.py`, `backend/tests/services/auth/test_oidc.py` (create `backend/tests/services/auth/__init__.py` only if sibling test packages have one)
- Modify: `backend/app/config.py`

**Interfaces:**
- Consumes: `assert_public_host(host)` from `app.services.dns_checks.ssrf_guard`.
- Produces:
  - `class OidcError(Exception)`
  - `@dataclass(frozen=True) class Provider: issuer: str; client_id: str; client_secret: str; allow_private: bool = False`
  - `async def discover(provider: Provider) -> dict`
  - `def authorization_url(discovery: dict, provider: Provider, *, redirect_uri: str, state: str, nonce: str, code_challenge: str, login_hint: str | None = None, extra: dict[str, str] | None = None) -> str`
  - `async def exchange_code(discovery: dict, provider: Provider, *, code: str, code_verifier: str, redirect_uri: str) -> dict`
  - `async def validate_id_token(discovery: dict, provider: Provider, id_token: str, *, nonce: str) -> dict`
  - `def normalize_issuer(value: str) -> str` (strips whitespace and one trailing slash; raises OidcError unless https, or http with allow_private)
  - module attribute `client_factory: Callable[[], httpx.AsyncClient]` (tests swap it for a MockTransport client)
  - `settings.oidc_allow_private_issuers: bool = False`

- [ ] **Step 1: Config**

In `app/config.py` next to the Entra SSO settings:

```python
    # OpenID Connect providers on a private address (10.x, 192.168.x, ...)
    # are refused unless this is on: an org admin could otherwise make the
    # server contact internal addresses. Turn on only when every
    # organization on this server is trusted. Also allows http:// issuers.
    oidc_allow_private_issuers: bool = False
```

- [ ] **Step 2: Write the failing tests**

```python
# backend/tests/services/auth/test_oidc.py
"""The generic OIDC client against a fake provider: locally generated keys,
discovery and JWKS served by an httpx MockTransport."""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.auth import oidc

ISSUER = "https://idp.example.com/realms/acme"
CLIENT = "yadt"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk() -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
    return {**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}


DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/auth",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/certs",
    "id_token_signing_alg_values_supported": ["RS256"],
}


def _token(key=KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": CLIENT, "sub": "user-1", "iat": now, "exp": now + 300, "nonce": "n1",
        "email": "jan@acme.example", "email_verified": True, **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def fake_idp(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=DISCOVERY)
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [_jwk()]})
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"id_token": _token(), "access_token": "a"})
        return httpx.Response(404)

    async def _public(host):
        return None

    oidc.clear_caches()
    monkeypatch.setattr(oidc, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(oidc, "assert_public_host", _public)
    return requests


PROVIDER = oidc.Provider(issuer=ISSUER, client_id=CLIENT, client_secret="s3cret")


async def test_a_valid_id_token_is_accepted():
    doc = await oidc.discover(PROVIDER)
    claims = await oidc.validate_id_token(doc, PROVIDER, _token(), nonce="n1")
    assert claims["sub"] == "user-1"


@pytest.mark.parametrize(
    "overrides,nonce",
    [
        ({"iss": "https://evil.example"}, "n1"),
        ({"aud": "someone-else"}, "n1"),
        ({"exp": int(time.time()) - 3600}, "n1"),
        ({}, "other-nonce"),
        ({"sub": None}, "n1"),
    ],
)
async def test_a_wrong_token_is_refused(overrides, nonce):
    doc = await oidc.discover(PROVIDER)
    with pytest.raises(oidc.OidcError):
        await oidc.validate_id_token(doc, PROVIDER, _token(**overrides), nonce=nonce)


async def test_a_token_signed_with_another_key_is_refused():
    doc = await oidc.discover(PROVIDER)
    with pytest.raises(oidc.OidcError):
        await oidc.validate_id_token(doc, PROVIDER, _token(key=OTHER_KEY), nonce="n1")


async def test_several_audiences_need_azp_to_be_us():
    doc = await oidc.discover(PROVIDER)
    with pytest.raises(oidc.OidcError):
        await oidc.validate_id_token(doc, PROVIDER, _token(aud=[CLIENT, "other"]), nonce="n1")
    claims = await oidc.validate_id_token(doc, PROVIDER, _token(aud=[CLIENT, "other"], azp=CLIENT), nonce="n1")
    assert claims["sub"] == "user-1"


async def test_discovery_must_name_the_same_issuer(fake_idp, monkeypatch):
    with pytest.raises(oidc.OidcError):
        await oidc.discover(oidc.Provider(issuer="https://idp.example.com/realms/other", client_id=CLIENT, client_secret="s"))


async def test_a_private_issuer_is_refused_unless_allowed(monkeypatch):
    from app.services.dns_checks.ssrf_guard import BlockedAddressError

    async def _private(host):
        raise BlockedAddressError(f"{host} resolves to a non-public address")

    monkeypatch.setattr(oidc, "assert_public_host", _private)
    with pytest.raises(oidc.OidcError, match="non-public"):
        await oidc.discover(PROVIDER)
    allowed = oidc.Provider(issuer=ISSUER, client_id=CLIENT, client_secret="s", allow_private=True)
    assert (await oidc.discover(allowed))["issuer"] == ISSUER


def test_issuers_must_be_https():
    assert oidc.normalize_issuer(" https://idp.example.com/realms/acme/ ") == ISSUER
    with pytest.raises(oidc.OidcError):
        oidc.normalize_issuer("http://idp.example.com")
    assert oidc.normalize_issuer("http://10.0.0.5:9000/application/o/yadt/", allow_private=True) == "http://10.0.0.5:9000/application/o/yadt"


async def test_the_code_exchange_sends_the_secret_and_verifier(fake_idp):
    doc = await oidc.discover(PROVIDER)
    tokens = await oidc.exchange_code(doc, PROVIDER, code="c", code_verifier="v", redirect_uri="https://yadt.example/cb")
    assert "id_token" in tokens
    body = fake_idp[-1].content.decode()
    assert "client_secret=s3cret" in body and "code_verifier=v" in body


async def test_the_authorization_url_carries_pkce_nonce_and_hint():
    doc = await oidc.discover(PROVIDER)
    url = oidc.authorization_url(
        doc, PROVIDER, redirect_uri="https://yadt.example/cb", state="s", nonce="n", code_challenge="c", login_hint="jan@acme.example"
    )
    assert url.startswith(f"{ISSUER}/auth?")
    for part in ("code_challenge_method=S256", "nonce=n", "login_hint=jan%40acme.example", "scope=openid+email+profile"):
        assert part in url
```

- [ ] **Step 3: Run — expect ImportError / failures**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/services/auth/test_oidc.py` → FAIL.

- [ ] **Step 4: Implement**

```python
# backend/app/services/auth/oidc.py
"""A generic OpenID Connect client: Google sign-in and organizations' own
providers (Authentik, Keycloak, ...). Microsoft keeps entra_oidc.py, which
handles its multi-tenant issuers.

Every request to an issuer is checked against private addresses first
(unless Provider.allow_private), never follows redirects and reads at most
MAX_RESPONSE_BYTES: the issuer URL can come from an org admin."""

import dataclasses
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from app.services.dns_checks.ssrf_guard import BlockedAddressError, assert_public_host

CACHE_SECONDS = 3600
MAX_RESPONSE_BYTES = 1_000_000
LEEWAY_SECONDS = 60
_ALLOWED_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}

client_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=10, follow_redirects=False)

_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, jwt.PyJWKSet]] = {}


class OidcError(Exception):
    """Anything that makes a sign-in through this provider impossible; the
    message is safe to show to the admin setting it up."""


@dataclasses.dataclass(frozen=True)
class Provider:
    issuer: str
    client_id: str
    client_secret: str
    allow_private: bool = False


def clear_caches() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()


def normalize_issuer(value: str, *, allow_private: bool = False) -> str:
    issuer = value.strip()
    if issuer.endswith("/"):
        issuer = issuer[:-1]
    parts = urlsplit(issuer)
    allowed_schemes = {"https", "http"} if allow_private else {"https"}
    if parts.scheme not in allowed_schemes or not parts.hostname or parts.query or parts.fragment:
        raise OidcError("the issuer must be an https:// address, for example https://auth.example.com/application/o/yadt")
    return issuer


async def _checked_url(url: str, provider: Provider) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" and not (provider.allow_private and parts.scheme == "http"):
        raise OidcError(f"{url} isn't an https:// address")
    if not provider.allow_private:
        try:
            await assert_public_host(parts.hostname or "")
        except BlockedAddressError as exc:
            raise OidcError(str(exc)) from exc
    return url


def _json(response: httpx.Response, what: str) -> dict:
    if response.status_code != 200:
        raise OidcError(f"{what} answered HTTP {response.status_code}")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise OidcError(f"{what} is too large")
    try:
        data = response.json()
    except ValueError as exc:
        raise OidcError(f"{what} isn't JSON") from exc
    if not isinstance(data, dict):
        raise OidcError(f"{what} isn't a JSON object")
    return data


async def _get(url: str, provider: Provider, what: str) -> dict:
    await _checked_url(url, provider)
    try:
        async with client_factory() as client:
            return _json(await client.get(url), what)
    except httpx.HTTPError as exc:
        raise OidcError(f"couldn't reach {what}: {exc.__class__.__name__}") from exc


async def discover(provider: Provider) -> dict:
    cached = _discovery_cache.get(provider.issuer)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    doc = await _get(f"{provider.issuer}/.well-known/openid-configuration", provider, "the discovery document")
    if doc.get("issuer") not in (provider.issuer, provider.issuer + "/"):
        raise OidcError(f"the discovery document is for {doc.get('issuer')!r}, not {provider.issuer!r}")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(key), str):
            raise OidcError(f"the discovery document has no {key}")
    _discovery_cache[provider.issuer] = (time.monotonic(), doc)
    return doc


def authorization_url(
    discovery: dict,
    provider: Provider,
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    login_hint: str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        **({"login_hint": login_hint} if login_hint else {}),
        **(extra or {}),
    }
    return f"{discovery['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(discovery: dict, provider: Provider, *, code: str, code_verifier: str, redirect_uri: str) -> dict:
    url = await _checked_url(discovery["token_endpoint"], provider)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
    }
    try:
        async with client_factory() as client:
            tokens = _json(await client.post(url, data=data), "the token endpoint")
    except httpx.HTTPError as exc:
        raise OidcError(f"couldn't reach the token endpoint: {exc.__class__.__name__}") from exc
    if not isinstance(tokens.get("id_token"), str):
        raise OidcError("the token endpoint returned no id_token")
    return tokens


async def _jwks(discovery: dict, provider: Provider, *, refresh: bool = False) -> jwt.PyJWKSet:
    uri = discovery["jwks_uri"]
    cached = _jwks_cache.get(uri)
    if cached and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        keys = jwt.PyJWKSet.from_dict(await _get(uri, provider, "the signing keys"))
    except jwt.PyJWTError as exc:
        raise OidcError(f"the signing keys can't be read: {exc}") from exc
    _jwks_cache[uri] = (time.monotonic(), keys)
    return keys


async def _signing_key(discovery: dict, provider: Provider, id_token: str) -> jwt.PyJWK:
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
    except jwt.PyJWTError as exc:
        raise OidcError(f"malformed id_token: {exc}") from exc
    for refresh in (False, True):  # a new key: fetch the set once more
        keys = await _jwks(discovery, provider, refresh=refresh)
        matches = [k for k in keys.keys if kid is None or k.key_id == kid]
        if matches:
            return matches[0]
    raise OidcError("the id_token is signed with an unknown key")


async def validate_id_token(discovery: dict, provider: Provider, id_token: str, *, nonce: str) -> dict:
    key = await _signing_key(discovery, provider, id_token)
    supported = set(discovery.get("id_token_signing_alg_values_supported") or ["RS256"])
    algorithms = sorted(supported & _ALLOWED_ALGORITHMS) or ["RS256"]
    try:
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=algorithms,
            audience=provider.client_id,
            issuer=discovery["issuer"],
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OidcError(f"the id_token isn't valid: {exc}") from exc
    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if len(audiences) > 1 and claims.get("azp") != provider.client_id:
        raise OidcError("the id_token is meant for more than this app")
    if not isinstance(claims.get("nonce"), str) or not _equal(claims["nonce"], nonce):
        raise OidcError("the id_token doesn't belong to this sign-in")
    return claims


def _equal(a: str, b: str) -> bool:
    import secrets

    return secrets.compare_digest(a.encode(), b.encode())
```

- [ ] **Step 5: Run the tests — expect PASS**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/services/auth/test_oidc.py`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/auth/oidc.py backend/app/config.py backend/tests/services/auth
git commit -m "Generic OpenID Connect client with private-address guard"
```

---

### Task 3: Shared finish step; Microsoft moves onto it

**Files:**
- Create: `backend/app/services/auth/sign_in_flow.py`
- Modify: `backend/app/routers/auth.py` (callback), `backend/app/repositories/auth.py`, `backend/app/services/auth/entra_oidc.py` (login_hint)
- Test: existing `backend/tests/routers/test_auth.py`, `tests/routers/test_review_round_2.py` must stay green; add one test for the Microsoft login hint.

**Interfaces:**
- Produces:
  - `get_user_by_subject(db, organization_id: UUID, method: AuthMethod, subject: str) -> User | None` in `app/repositories/auth.py` (Microsoft: `entra_object_id`; google/oidc: `external_subject` + `auth_method`).
  - `@dataclass class SignInOutcome: raw_token: str | None; error: str | None`
  - `async def complete_sign_in(db, *, org: Organization, method: AuthMethod, subject: str, email: str, display_name: str | None, ip_address: str | None, user_agent: str | None) -> SignInOutcome`
  - `def session_redirect(outcome: SignInOutcome, *, clear_cookies: tuple[str, ...]) -> RedirectResponse` (success: 302 to `/` with the session cookie; failure: 302 to `/login?error=<code>`)
  - `entra_oidc.build_authorization_url(..., login_hint: str | None = None)` and `GET /api/auth/login?login_hint=`.

- [ ] **Step 1: Write the failing hint test** (append to `tests/routers/test_auth.py`)

```python
async def test_microsoft_login_passes_the_email_hint(api, monkeypatch):
    client, _owner_factory = api
    monkeypatch.setattr("app.routers.auth.settings.entra_sso_client_id", "client-id")
    response = await client.get("/api/auth/login", params={"login_hint": "jan@acme.example"}, follow_redirects=False)
    assert response.status_code == 302 and "login_hint=jan%40acme.example" in response.headers["location"]
```

- [ ] **Step 2: Run — expect FAIL** (`login_hint` not in the URL).

- [ ] **Step 3: Repository lookup**

```python
# app/repositories/auth.py
async def get_user_by_subject(db: AsyncSession, organization_id: UUID, method: AuthMethod, subject: str) -> User | None:
    """The user a provider's stable ID belongs to: entra_object_id for
    Microsoft, external_subject for Google and OIDC."""
    if method == AuthMethod.entra:
        condition = User.entra_object_id == subject
    else:
        condition = (User.auth_method == method) & (User.external_subject == subject)
    result = await db.execute(select(User).where(User.organization_id == organization_id, condition))
    return result.scalar_one_or_none()


async def get_organization_by_google_domain(db: AsyncSession, domain: str) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.google_workspace_domain == domain.lower()))
    return result.scalar_one_or_none()


async def get_organization_by_slug(db: AsyncSession, slug: str) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.slug == slug.lower()))
    return result.scalar_one_or_none()
```

- [ ] **Step 4: The shared finish step**

```python
# backend/app/services/auth/sign_in_flow.py
"""The end of every provider sign-in (Microsoft, Google, OIDC), once the
provider's token has been checked: find or create the user, refuse
suspended organizations and disabled users, log it, start a session."""

import dataclasses
from datetime import datetime, timezone

from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context
from app.models.enums import AuthMethod, OrganizationStatus, SignInResult, UserRole, UserStatus
from app.models.organization import Organization
from app.models.user import User
from app.repositories.auth import get_user_by_subject
from app.services.auth import session_manager
from app.services.auth.session_manager import cookie_kwargs
from app.services.auth.sign_in_log import record_sign_in_event


@dataclasses.dataclass
class SignInOutcome:
    raw_token: str | None
    error: str | None


async def complete_sign_in(
    db: AsyncSession,
    *,
    org: Organization,
    method: AuthMethod,
    subject: str,
    email: str,
    display_name: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> SignInOutcome:
    await set_org_context(db, org.id)
    log = dict(auth_method=method, organization_id=org.id, attempted_email=email, ip_address=ip_address, user_agent=user_agent)

    if org.status == OrganizationStatus.suspended:
        await record_sign_in_event(db, result=SignInResult.failure, failure_reason="organization_suspended", **log)
        await db.commit()
        return SignInOutcome(None, "organization_suspended")

    user = await get_user_by_subject(db, org.id, method, subject)
    if user is None:
        # Only the expected admin becomes org admin, whichever order people
        # sign in; without one set, nobody does (the platform admin then
        # sets it or makes someone admin).
        expected = (org.expected_admin_email or "").strip().lower()
        is_expected_admin = bool(expected) and email.strip().lower() == expected
        user = User(
            organization_id=org.id,
            auth_method=method,
            email=email,
            display_name=display_name,
            role=UserRole.org_admin if is_expected_admin else UserRole.member,
        )
        if method == AuthMethod.entra:
            user.entra_object_id = subject
        else:
            user.external_subject = subject
        db.add(user)
        await db.flush()
    else:
        if user.status != UserStatus.active:
            await record_sign_in_event(db, result=SignInResult.failure, user_id=user.id, failure_reason="user_disabled", **log)
            await db.commit()
            return SignInOutcome(None, "user_disabled")
        user.email = email
        user.display_name = display_name

    user.last_login_at = datetime.now(timezone.utc)
    _, raw_token = await session_manager.create_user_session(
        db, user_id=user.id, organization_id=org.id, ip_address=ip_address, user_agent=user_agent
    )
    await record_sign_in_event(db, result=SignInResult.success, user_id=user.id, **log)
    await db.commit()
    return SignInOutcome(raw_token, None)


def session_redirect(outcome: SignInOutcome, *, clear_cookies: tuple[str, ...]) -> RedirectResponse:
    if outcome.error:
        response = RedirectResponse(f"/login?error={outcome.error}", status_code=302)
    else:
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            settings.session_cookie_name,
            outcome.raw_token,
            max_age=settings.session_idle_timeout_hours * 3600,
            **cookie_kwargs(),
        )
    for name in clear_cookies:
        response.delete_cookie(name, path="/")
    return response
```

Check `User` creation for Microsoft previously didn't set `auth_method` (column default `entra`); setting it explicitly is the same value.

- [ ] **Step 5: Use it in the Microsoft callback**

In `app/routers/auth.py` `callback`, replace everything from `# Moved up from after the suspended check below` through the end of the function with:

```python
    outcome = await complete_sign_in(
        db, org=org, method=AuthMethod.entra, subject=object_id, email=email, display_name=display_name,
        ip_address=ip_address, user_agent=user_agent,
    )
    return session_redirect(outcome, clear_cookies=(_OAUTH_STATE_COOKIE, _OAUTH_VERIFIER_COOKIE, _OAUTH_NONCE_COOKIE))
```

Import `complete_sign_in, session_redirect` from `app.services.auth.sign_in_flow`; remove now-unused imports (`get_user_by_org_and_entra_object_id`, `UserRole`, `UserStatus` only if nothing else in the file uses them — check with grep).

`login()` gains `login_hint: str | None = None` (query parameter, max 320 chars: `Query(default=None, max_length=320)`), passed to `entra_oidc.build_authorization_url(..., login_hint=login_hint)`, which adds `"login_hint": login_hint` to `params` when set.

- [ ] **Step 6: Run the auth suites — expect PASS**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_auth.py tests/routers/test_review_round_2.py tests/rls`

- [ ] **Step 7: Commit**

```bash
git add backend/app backend/tests/routers/test_auth.py
git commit -m "Sign-in: one finish step for every provider; Microsoft login takes an email hint"
```

---

### Task 4: Google and per-organization OIDC sign-in routes

**Files:**
- Create: `backend/app/services/auth/oauth_cookies.py`, `backend/app/routers/auth_providers.py`, `backend/tests/routers/test_auth_providers.py`
- Modify: `backend/app/config.py`, `backend/app/main.py` (include the router the same way `auth.router` is included)

**Interfaces:**
- Consumes: Task 2 (`oidc.*`), Task 3 (`complete_sign_in`, `session_redirect`, `get_organization_by_google_domain`, `get_organization_by_slug`).
- Produces:
  - Settings: `google_sso_client_id: str | None = None`, `google_sso_client_secret: str | None = None`, properties `google_sso_redirect_uri` (`{public_base_url}/api/auth/google/callback`) and `oidc_redirect_uri` (`{public_base_url}/api/auth/oidc/callback`).
  - `GOOGLE_ISSUER = "https://accounts.google.com"`; `google_provider() -> oidc.Provider`; `org_provider(org) -> oidc.Provider`.
  - Routes: `GET /api/auth/google/login?login_hint=`, `GET /api/auth/google/callback`, `GET /api/auth/oidc/start?org=<slug>&login_hint=`, `GET /api/auth/oidc/callback`.
  - `oauth_cookies.start(response, *, state, verifier, nonce, org_id: str | None = None, mode: str = "sign_in")`, `oauth_cookies.read(request) -> OAuthCookies | None` (`OAuthCookies(verifier, nonce, org_id, mode)`; None when state missing or not equal to the query `state`), `oauth_cookies.NAMES` (tuple of cookie names to clear).
  - Helper `async def verified_domain_names(db, organization_id) -> list[str]` in `app/repositories/domains.py` (names of the org's domains with `verification_status == verified`, lowercase).
  - Helper `def email_on_domains(email: str, domains: list[str]) -> bool` in `auth_providers.py`.
  - Failure reasons logged: `invalid_state`, `token_invalid`, `email_not_verified`, `hd_mismatch`, `organization_not_provisioned`, `email_domain_not_verified`, `provider_unavailable`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/routers/test_auth_providers.py
"""Google and per-organization OIDC sign-in, with the provider faked at the
oidc module boundary (its own checks are tested in test_oidc.py)."""

import pytest
from sqlalchemy import select

from app.models.domain import Domain
from app.models.enums import AuthMethod, DomainVerificationStatus, OrganizationStatus
from app.models.organization import Organization
from app.models.user import User
from app.services.auth import oidc


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


# --- Google --------------------------------------------------------------

async def test_google_signs_in_a_workspace_user(api, fake_provider, monkeypatch):
    client, owner_factory = api
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_id", "g-client")
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_secret", "g-secret")
    org = await _org(owner_factory, google_workspace_domain="acme.example", expected_admin_email="boss@acme.example")
    fake_provider["claims"] = {"sub": "g-1", "email": "Boss@acme.example", "email_verified": True, "hd": "acme.example", "name": "Boss"}

    _set_flow_cookies(client)
    response = await client.get("/api/auth/google/callback", params={"code": "c", "state": "matching-state"}, follow_redirects=False)

    assert response.status_code == 302 and response.headers["location"] == "/"
    me = (await client.get("/api/auth/me")).json()
    assert me["role"] == "org_admin"
    async with owner_factory() as db:
        user = (await db.execute(select(User).where(User.organization_id == org.id))).scalar_one()
        assert (user.auth_method, user.external_subject) == (AuthMethod.google, "g-1")


@pytest.mark.parametrize(
    "claims,reason",
    [
        ({"sub": "g-1", "email": "a@gmail.com", "email_verified": True}, "hd_mismatch"),
        ({"sub": "g-1", "email": "a@other.example", "email_verified": True, "hd": "other.example"}, "organization_not_provisioned"),
        ({"sub": "g-1", "email": "a@acme.example", "email_verified": False, "hd": "acme.example"}, "email_not_verified"),
    ],
)
async def test_google_refuses_accounts_outside_a_linked_workspace(api, fake_provider, monkeypatch, claims, reason):
    client, owner_factory = api
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_id", "g-client")
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_secret", "g-secret")
    await _org(owner_factory, google_workspace_domain="acme.example")
    fake_provider["claims"] = claims

    _set_flow_cookies(client)
    response = await client.get("/api/auth/google/callback", params={"code": "c", "state": "matching-state"}, follow_redirects=False)
    assert response.headers["location"] == f"/login?error={reason}"


async def test_google_login_is_404_without_the_app(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_id", None)
    assert (await client.get("/api/auth/google/login", follow_redirects=False)).status_code == 404


async def test_a_mismatched_state_is_refused(api, fake_provider, monkeypatch):
    client, _ = api
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_id", "g-client")
    _set_flow_cookies(client)
    response = await client.get("/api/auth/google/callback", params={"code": "c", "state": "other"}, follow_redirects=False)
    assert response.headers["location"] == "/login?error=invalid_state"


# --- organization OIDC ----------------------------------------------------

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
    response = await client.get("/api/auth/oidc/callback", params={"code": "c", "state": "matching-state"}, follow_redirects=False)

    assert response.headers["location"] == "/"
    assert fake_provider["provider"].issuer == "https://idp.acme.example"
    assert (await client.get("/api/auth/me")).json()["role"] == "member"


async def test_oidc_refuses_an_email_outside_the_orgs_verified_domains(api, fake_provider):
    client, owner_factory = api
    org = await _oidc_org(owner_factory)
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@evil.example", "email_verified": True}

    _set_flow_cookies(client, str(org.id))
    response = await client.get("/api/auth/oidc/callback", params={"code": "c", "state": "matching-state"}, follow_redirects=False)
    assert response.headers["location"] == "/login?error=email_domain_not_verified"


async def test_oidc_checks_against_the_org_in_the_cookie_not_the_response(api, fake_provider):
    # Another org's provider can't sign anyone into this one: the token is
    # checked with the settings of the org the cookie names.
    client, owner_factory = api
    acme = await _oidc_org(owner_factory)
    other = await _org(owner_factory, name="Other", slug="other", oidc_issuer="https://idp.other.example", oidc_client_id="o", oidc_client_secret="s")
    await _verified_domain(owner_factory, other, "other.example")
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@other.example", "email_verified": True}

    _set_flow_cookies(client, str(acme.id))
    response = await client.get("/api/auth/oidc/callback", params={"code": "c", "state": "matching-state"}, follow_redirects=False)
    assert fake_provider["provider"].issuer == "https://idp.acme.example"
    assert response.headers["location"] == "/login?error=email_domain_not_verified"


async def test_oidc_start_for_an_unknown_org_goes_back_to_login(api, fake_provider):
    client, _ = api
    response = await client.get("/api/auth/oidc/start", params={"org": "nope"}, follow_redirects=False)
    assert response.headers["location"] == "/login?error=organization_not_provisioned"
```

- [ ] **Step 2: Run — expect 404s/FAIL**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_auth_providers.py`

- [ ] **Step 3: Config** (in `app/config.py` next to Entra SSO)

```python
    # "Sign in with Google": one Google OAuth client for the whole server
    # (Google Cloud console → APIs & Services → Credentials → OAuth client
    # ID, type "Web application", redirect URI <PUBLIC_BASE_URL>/api/auth/google/callback).
    google_sso_client_id: str | None = None
    google_sso_client_secret: str | None = None

    @property
    def google_sso_redirect_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/google/callback"

    @property
    def oidc_redirect_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/oidc/callback"
```

(Put the properties with the other `@property` definitions.)

- [ ] **Step 4: Flow cookies**

```python
# backend/app/services/auth/oauth_cookies.py
"""The short-lived cookies that tie a provider's answer to the browser that
started the sign-in: state (CSRF), PKCE verifier, nonce, and for an
organization's own provider which organization it was started for."""

import dataclasses
import secrets

from fastapi import Request, Response

from app.services.auth.session_manager import cookie_kwargs

STATE, VERIFIER, NONCE, ORG, MODE = "oauth_state", "oauth_verifier", "oauth_nonce", "oauth_org", "oauth_mode"
NAMES = (STATE, VERIFIER, NONCE, ORG, MODE)
_MAX_AGE = 600


@dataclasses.dataclass(frozen=True)
class OAuthCookies:
    verifier: str
    nonce: str
    org_id: str | None
    mode: str


def start(response: Response, *, state: str, verifier: str, nonce: str, org_id: str | None = None, mode: str = "sign_in") -> None:
    short_lived = {**cookie_kwargs(), "max_age": _MAX_AGE}
    response.set_cookie(STATE, state, **short_lived)
    response.set_cookie(VERIFIER, verifier, **short_lived)
    response.set_cookie(NONCE, nonce, **short_lived)
    response.set_cookie(MODE, mode, **short_lived)
    if org_id:
        response.set_cookie(ORG, org_id, **short_lived)
    else:
        response.delete_cookie(ORG, path="/")


def read(request: Request) -> OAuthCookies | None:
    state = request.query_params.get("state")
    cookie_state = request.cookies.get(STATE)
    verifier = request.cookies.get(VERIFIER)
    nonce = request.cookies.get(NONCE)
    if not state or not cookie_state or not verifier or not nonce:
        return None
    if not secrets.compare_digest(state.encode(), cookie_state.encode()):
        return None
    return OAuthCookies(verifier, nonce, request.cookies.get(ORG), request.cookies.get(MODE) or "sign_in")
```

- [ ] **Step 5: Verified domain names** (append to `app/repositories/domains.py`)

```python
async def verified_domain_names(db: AsyncSession, organization_id: UUID) -> list[str]:
    result = await db.execute(
        select(Domain.name).where(
            Domain.organization_id == organization_id,
            Domain.verification_status == DomainVerificationStatus.verified,
        )
    )
    return [name.lower() for name in result.scalars()]
```

(Add missing imports in that file only if not already present.)

- [ ] **Step 6: The routes**

```python
# backend/app/routers/auth_providers.py
"""Sign-in with Google Workspace and with an organization's own OpenID
Connect provider. Microsoft stays in auth.py. Both end in
sign_in_flow.complete_sign_in."""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import get_db
from app.models.enums import AuthMethod, OrganizationStatus, SignInResult
from app.models.organization import Organization
from app.repositories.auth import get_organization_by_google_domain, get_organization_by_slug
from app.repositories.domains import verified_domain_names
from app.repositories.organizations import get_organization
from app.services.auth import oauth_cookies, oidc, pkce
from app.services.auth.sign_in_flow import complete_sign_in, session_redirect
from app.services.auth.sign_in_log import client_network_info, record_sign_in_event

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_ISSUER = "https://accounts.google.com"


def google_provider() -> oidc.Provider:
    return oidc.Provider(
        issuer=GOOGLE_ISSUER, client_id=settings.google_sso_client_id or "", client_secret=settings.google_sso_client_secret or ""
    )


def org_provider(org: Organization) -> oidc.Provider:
    return oidc.Provider(
        issuer=org.oidc_issuer or "",
        client_id=org.oidc_client_id or "",
        client_secret=org.oidc_client_secret or "",
        allow_private=settings.oidc_allow_private_issuers,
    )


def email_on_domains(email: str, domains: list[str]) -> bool:
    domain = email.rpartition("@")[2].strip().lower().rstrip(".")
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)


def _back_to_login(error: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={error}", status_code=302)
    for name in oauth_cookies.NAMES:
        response.delete_cookie(name, path="/")
    return response


async def _failure(db, request, method: AuthMethod, reason: str, *, org_id=None, email=None) -> RedirectResponse:
    ip_address, user_agent = client_network_info(request)
    await record_sign_in_event(
        db, result=SignInResult.failure, auth_method=method, organization_id=org_id, attempted_email=email,
        failure_reason=reason, ip_address=ip_address, user_agent=user_agent,
    )
    await db.commit()
    return _back_to_login(reason)


async def _redirect_to_provider(provider: oidc.Provider, *, redirect_uri: str, login_hint: str | None, org_id: str | None,
                                mode: str = "sign_in", extra: dict[str, str] | None = None) -> RedirectResponse:
    doc = await oidc.discover(provider)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), pkce.generate_verifier()
    url = oidc.authorization_url(
        doc, provider, redirect_uri=redirect_uri, state=state, nonce=nonce,
        code_challenge=pkce.derive_challenge(verifier), login_hint=login_hint, extra=extra,
    )
    response = RedirectResponse(url, status_code=302)
    oauth_cookies.start(response, state=state, verifier=verifier, nonce=nonce, org_id=org_id, mode=mode)
    return response


async def _checked_claims(provider: oidc.Provider, request: Request, cookies, redirect_uri: str) -> dict:
    """Raises oidc.OidcError when the provider's answer doesn't check out."""
    if request.query_params.get("error") or not request.query_params.get("code"):
        raise oidc.OidcError(request.query_params.get("error") or "no code")
    doc = await oidc.discover(provider)
    tokens = await oidc.exchange_code(
        doc, provider, code=request.query_params["code"], code_verifier=cookies.verifier, redirect_uri=redirect_uri
    )
    return await oidc.validate_id_token(doc, provider, tokens["id_token"], nonce=cookies.nonce)


# --- Google ------------------------------------------------------------------

@router.get("/google/login")
async def google_login(login_hint: str | None = Query(default=None, max_length=320)) -> RedirectResponse:
    if not settings.google_sso_client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        # hd=*: only Google Workspace accounts, not personal Gmail.
        return await _redirect_to_provider(
            google_provider(), redirect_uri=settings.google_sso_redirect_uri, login_hint=login_hint, org_id=None,
            extra={"hd": "*"},
        )
    except oidc.OidcError:
        return _back_to_login("provider_unavailable")


@router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    method = AuthMethod.google
    cookies = oauth_cookies.read(request)
    if cookies is None or not settings.google_sso_client_id:
        return await _failure(db, request, method, "invalid_state")
    try:
        claims = await _checked_claims(google_provider(), request, cookies, settings.google_sso_redirect_uri)
    except (oidc.OidcError, KeyError):
        return await _failure(db, request, method, "token_invalid")

    email = str(claims.get("email") or "")
    if claims.get("email_verified") is not True:
        return await _failure(db, request, method, "email_not_verified", email=email)
    workspace = str(claims.get("hd") or "").lower()
    if not workspace:
        return await _failure(db, request, method, "hd_mismatch", email=email)
    org = await get_organization_by_google_domain(db, workspace)
    if org is None:
        return await _failure(db, request, method, "organization_not_provisioned", email=email)

    ip_address, user_agent = client_network_info(request)
    outcome = await complete_sign_in(
        db, org=org, method=method, subject=str(claims["sub"]), email=email, display_name=claims.get("name"),
        ip_address=ip_address, user_agent=user_agent,
    )
    return session_redirect(outcome, clear_cookies=oauth_cookies.NAMES)


# --- an organization's own provider ----------------------------------------------

@router.get("/oidc/start")
async def oidc_start(
    org: str = Query(max_length=63), login_hint: str | None = Query(default=None, max_length=320), db: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    organization = await get_organization_by_slug(db, org)
    if organization is None or organization.sign_in_provider != "oidc" or organization.status != OrganizationStatus.active:
        return _back_to_login("organization_not_provisioned")
    try:
        return await _redirect_to_provider(
            org_provider(organization), redirect_uri=settings.oidc_redirect_uri, login_hint=login_hint, org_id=str(organization.id)
        )
    except oidc.OidcError:
        return _back_to_login("provider_unavailable")


@router.get("/oidc/callback")
async def oidc_callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    method = AuthMethod.oidc
    cookies = oauth_cookies.read(request)
    if cookies is None or not cookies.org_id:
        return await _failure(db, request, method, "invalid_state")
    if cookies.mode == "test":
        from app.routers.organizations import finish_oidc_test  # Task 6

        return await finish_oidc_test(request, db, cookies)

    try:
        org = await get_organization(db, uuid.UUID(cookies.org_id))
    except (ValueError, HTTPException):
        org = None
    if org is None or org.sign_in_provider != "oidc":
        return await _failure(db, request, method, "organization_not_provisioned")
    try:
        claims = await _checked_claims(org_provider(org), request, cookies, settings.oidc_redirect_uri)
    except (oidc.OidcError, KeyError):
        return await _failure(db, request, method, "token_invalid", org_id=org.id)

    email = str(claims.get("email") or "")
    if not email or claims.get("email_verified") is False:
        return await _failure(db, request, method, "email_not_verified", org_id=org.id, email=email)
    await set_org_context(db, org.id)
    if not email_on_domains(email, await verified_domain_names(db, org.id)):
        return await _failure(db, request, method, "email_domain_not_verified", org_id=org.id, email=email)

    ip_address, user_agent = client_network_info(request)
    outcome = await complete_sign_in(
        db, org=org, method=method, subject=str(claims["sub"]), email=email,
        display_name=claims.get("name") or claims.get("preferred_username"), ip_address=ip_address, user_agent=user_agent,
    )
    return session_redirect(outcome, clear_cookies=oauth_cookies.NAMES)
```

Check `get_organization`'s behaviour for a missing id (it may raise 404 or return None) and adapt the `try` accordingly. Register the router in `app/main.py` exactly like `auth.router` (same prefix handling).

Until Task 6 exists, the `mode == "test"` branch is unreachable in tests; keep the import inside the branch.

- [ ] **Step 7: Run — expect PASS**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_auth_providers.py tests/routers/test_auth.py`

- [ ] **Step 8: Commit**

```bash
git add backend/app backend/tests/routers/test_auth_providers.py
git commit -m "Sign in with Google Workspace or an organization's own OIDC provider"
```

---

### Task 5: Login-page routing — config, discover, direct link

**Files:**
- Modify: `backend/app/routers/auth_providers.py`, `backend/app/routers/auth.py` (`/auth/config`)
- Test: `backend/tests/routers/test_auth_providers.py` (append)

**Interfaces:**
- Produces:
  - `GET /api/auth/config` → `{"entra_sso_enabled": bool, "google_sso_enabled": bool, "company_sign_in_enabled": bool}` (last: any active organization has a provider).
  - `POST /api/auth/discover` body `{"email": str}` → `{"url": str}` or `{"url": null}`; rate-limited with `login_limiter`.
  - `GET /api/auth/org/{slug}` → `{"url": str}`; 404 when the slug has no provider.
  - `def sign_in_url(org: Organization, login_hint: str | None) -> str | None`.

- [ ] **Step 1: Failing tests** (append)

```python
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


async def test_discover_sends_microsoft_and_google_orgs_to_their_button(api, monkeypatch):
    import uuid as _uuid

    client, owner_factory = api
    monkeypatch.setattr("app.routers.auth_providers.settings.google_sso_client_id", "g")
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
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** (in `auth_providers.py`)

```python
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.db.rls import set_platform_admin_context
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.services.auth.rate_limit import login_limiter, rate_limiter
from app.services.ingestion.sender_auth import can_be_trusted


class DiscoverRequest(BaseModel):
    email: str = Field(max_length=320)


def sign_in_url(org: Organization, login_hint: str | None) -> str | None:
    hint = f"login_hint={urlencode({'': login_hint})[1:]}" if login_hint else ""
    provider = org.sign_in_provider
    if provider == "oidc":
        return f"/api/auth/oidc/start?{urlencode({'org': org.slug})}" + (f"&{hint}" if hint else "")
    if provider == "google" and settings.google_sso_client_id:
        return "/api/auth/google/login" + (f"?{hint}" if hint else "")
    if provider == "microsoft" and settings.entra_sso_client_id:
        return "/api/auth/login" + (f"?{hint}" if hint else "")
    return None


async def _org_for_email_domain(db: AsyncSession, domain: str) -> Organization | None:
    """The active organization with a provider that verified this domain or
    a parent of it (the longest match wins). Reads across organizations,
    so the bypass is on only for this one query."""
    labels = domain.split(".")
    candidates = [".".join(labels[i:]) for i in range(len(labels) - 1)]
    await set_platform_admin_context(db, is_admin=True)
    try:
        rows = (
            await db.execute(
                select(Domain.name, Organization)
                .join(Organization, Organization.id == Domain.organization_id)
                .where(
                    Domain.name.in_(candidates),
                    Domain.verification_status == DomainVerificationStatus.verified,
                    Organization.status == OrganizationStatus.active,
                    or_(
                        Organization.entra_tenant_id.is_not(None),
                        Organization.google_workspace_domain.is_not(None),
                        Organization.oidc_issuer.is_not(None),
                    ),
                )
            )
        ).all()
    finally:
        await set_platform_admin_context(db, is_admin=False)
    if not rows:
        return None
    return max(rows, key=lambda row: len(row[0]))[1]


@router.post("/discover", dependencies=[Depends(rate_limiter(login_limiter))])
async def discover_sign_in(body: DiscoverRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Where "Sign in with your company account" goes for this email. Never
    names the organization; anything unknown gets the same empty answer."""
    email = body.email.strip()
    local, _, domain = email.rpartition("@")
    domain = domain.lower().rstrip(".")
    if not local or "." not in domain or not can_be_trusted(domain):
        return {"url": None}
    org = await _org_for_email_domain(db, domain)
    return {"url": sign_in_url(org, email) if org else None}


@router.get("/org/{slug}")
async def org_sign_in(slug: str, db: AsyncSession = Depends(get_db)) -> dict:
    org = await get_organization_by_slug(db, slug)
    url = sign_in_url(org, None) if org and org.status == OrganizationStatus.active else None
    if url is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return {"url": url}
```

`can_be_trusted(domain)` returns False for public mailbox domains (`FREE_MAILBOX_DOMAINS`) — confirm in `sender_auth.py` that's its meaning; if it means something else, use `domain not in FREE_MAILBOX_DOMAINS` directly.

`/auth/config` in `auth.py`:

```python
@router.get("/config")
async def auth_config(db: AsyncSession = Depends(get_db)) -> dict:
    """Unauthenticated — the login page calls this before a user exists to
    decide which sign-in options to show."""
    company = (
        await db.execute(
            select(Organization.id).where(
                Organization.status == OrganizationStatus.active,
                or_(Organization.oidc_issuer.is_not(None),
                    Organization.google_workspace_domain.is_not(None) & bool(settings.google_sso_client_id),
                    Organization.entra_tenant_id.is_not(None) & bool(settings.entra_sso_client_id)),
            ).limit(1)
        )
    ).first()
    return {
        "entra_sso_enabled": bool(settings.entra_sso_client_id),
        "google_sso_enabled": bool(settings.google_sso_client_id),
        "company_sign_in_enabled": company is not None,
    }
```

If SQLAlchemy rejects `column & bool`, build the `or_` list in Python: start with `[Organization.oidc_issuer.is_not(None)]` and append the other two conditions only when the matching client ID is set. Update the existing `/auth/config` test expectations in `tests/routers/test_auth.py` (grep `entra_sso_enabled`) to the new shape.

- [ ] **Step 4: Run — expect PASS**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_auth_providers.py tests/routers/test_auth.py`

- [ ] **Step 5: Commit**

```bash
git add backend/app backend/tests/routers
git commit -m "Login: route a company email to its organization's sign-in; direct org links"
```

---

### Task 6: Provider settings — platform admin, org admin, test before save

**Files:**
- Create: `backend/app/services/auth/oidc_test.py`, `backend/tests/routers/test_sign_in_settings.py`
- Modify: `backend/app/routers/organizations.py`, `backend/app/schemas/platform_admin.py`, `backend/app/routers/platform_admin.py`, `backend/app/routers/users.py` and `platform_admin.py` local-user checks

**Interfaces:**
- Consumes: Tasks 2, 4 (`oidc`, `oauth_cookies`, `_redirect_to_provider`, `_checked_claims`, `org_provider`, `email_on_domains`).
- Produces:
  - `oidc_test.pending_cookie(settings: PendingOidc) -> str`, `oidc_test.read_pending(value: str) -> PendingOidc | None`, `oidc_test.ticket_for(pending: PendingOidc) -> str`, `oidc_test.ticket_matches(ticket: str, pending: PendingOidc) -> bool`; `@dataclass PendingOidc: org_id: str; issuer: str; client_id: str; client_secret: str`. Fernet via `app.services.crypto.secrets` with a 600 s TTL.
  - `GET /api/organizations/current/sign-in` → `{"provider", "slug", "login_link", "redirect_uri", "oidc": {"issuer","client_id","secret_set"} | null, "google_workspace_domain"}`.
  - `POST /api/organizations/current/sign-in/oidc/test` (org admin) body `{"issuer","client_id","client_secret"?}` → `{"start_url": "/api/auth/oidc/start-test"}` and sets cookie `oidc_test`.
  - `GET /api/auth/oidc/start-test` → redirect to the pending provider (mode `test`).
  - `finish_oidc_test(request, db, cookies) -> HTMLResponse` (called from the OIDC callback): a small page that `postMessage`s `{type: "yadt-oidc-test", ok: bool, ticket?: string, error?: string}` to `window.opener` (origin `settings.public_base_url`) and closes itself.
  - `PUT /api/organizations/current/sign-in/oidc` body `{"issuer","client_id","client_secret"?,"ticket"}` → the GET shape; 400 `"run Test sign-in first"` without a matching ticket; 409 when the org signs in with Microsoft or Google.
  - `DELETE /api/organizations/current/sign-in/oidc` → the GET shape.
  - Platform admin `OrganizationCreateRequest`/`OrganizationUpdateRequest` gain `slug`, `google_workspace_domain`, `oidc_issuer`, `oidc_client_id`, `oidc_client_secret`; create sets `slug=unique_slug(name, taken)` when not given; update/create return 409 `"an organization signs in with one provider"` when more than one would be set; the admin org JSON (`_org_out` or equivalent in `platform_admin.py`) adds `slug`, `sign_in_provider`, `google_workspace_domain`, `oidc_issuer`, `oidc_client_id`, `oidc_secret_set`.
  - Local teammates are allowed in every organization (remove the "no Entra tenant" guards for creating local users in `users.py` and `platform_admin.py`; grep for `entra_tenant_id` in those two files).

- [ ] **Step 1: Failing tests**

```python
# backend/tests/routers/test_sign_in_settings.py
import pytest

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus, UserRole
from app.services.auth import oidc, oidc_test
from tests.conftest import login_as, login_as_platform_admin, seed_org_and_user


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


async def _admin(client, owner_factory):
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="acme.example", verification_status=DomainVerificationStatus.verified))
        await db.commit()
    await login_as(client, owner_factory, user)
    return org


async def _run_test(client) -> str:
    started = await client.post("/api/organizations/current/sign-in/oidc/test", json=SETTINGS)
    assert started.status_code == 200, started.text
    redirect = await client.get(started.json()["start_url"], follow_redirects=False)
    assert redirect.status_code == 302 and redirect.headers["location"].startswith("https://idp.acme.example/auth")
    state = redirect.cookies["oauth_state"]
    done = await client.get("/api/auth/oidc/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert done.status_code == 200
    return done.text


async def test_saving_needs_a_passed_test(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory)

    refused = await client.put("/api/organizations/current/sign-in/oidc", json={**SETTINGS, "ticket": "nope"})
    assert refused.status_code == 400

    page = await _run_test(client)
    assert '"ok": true' in page
    ticket = page.split('"ticket": "')[1].split('"')[0]

    saved = await client.put("/api/organizations/current/sign-in/oidc", json={**SETTINGS, "ticket": ticket})
    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["provider"] == "oidc" and out["oidc"] == {"issuer": "https://idp.acme.example", "client_id": "yadt", "secret_set": True}
    assert "s3cret" not in saved.text


async def test_a_ticket_is_only_good_for_the_settings_it_tested(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory)
    page = await _run_test(client)
    ticket = page.split('"ticket": "')[1].split('"')[0]
    other = {**SETTINGS, "issuer": "https://idp.evil.example", "ticket": ticket}
    assert (await client.put("/api/organizations/current/sign-in/oidc", json=other)).status_code == 400


async def test_a_failed_test_reports_why(api, fake_provider):
    client, owner_factory = api
    await _admin(client, owner_factory)
    fake_provider["claims"] = {"sub": "kc-1", "email": "jan@elsewhere.example", "email_verified": True}
    page = await _run_test(client)
    assert '"ok": false' in page and "verified domains" in page


async def test_members_cant_change_sign_in(api, fake_provider):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)
    assert (await client.post("/api/organizations/current/sign-in/oidc/test", json=SETTINGS)).status_code == 403


async def test_platform_admin_sets_a_provider_and_only_one(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    created = await client.post("/api/admin/organizations", json={"name": "Acme B.V.", "google_workspace_domain": "acme.example"})
    assert created.status_code == 201, created.text
    org = created.json()
    assert org["slug"] == "acme-b-v" and org["sign_in_provider"] == "google"

    both = await client.patch(f"/api/admin/organizations/{org['id']}", json={"oidc_issuer": "https://idp.acme.example", "oidc_client_id": "c", "oidc_client_secret": "s"})
    assert both.status_code == 409
```

Adjust the platform admin create/patch HTTP verbs and status codes to the ones existing tests in `tests/routers/test_platform_admin.py` use (grep `"/api/admin/organizations"`).

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Pending settings and tickets**

```python
# backend/app/services/auth/oidc_test.py
"""Test sign-in for OIDC settings that aren't saved yet: the settings ride
in an encrypted cookie for ten minutes, and a passed test hands back a
ticket that only matches those exact settings, so saving can require it."""

import dataclasses
import hashlib
import json

from cryptography.fernet import InvalidToken

from app.services.crypto.secrets import _fernet

TTL_SECONDS = 600
COOKIE = "oidc_test"


@dataclasses.dataclass(frozen=True)
class PendingOidc:
    org_id: str
    issuer: str
    client_id: str
    client_secret: str


def _digest(pending: PendingOidc) -> str:
    return hashlib.sha256(json.dumps(dataclasses.asdict(pending), sort_keys=True).encode()).hexdigest()


def pending_cookie(pending: PendingOidc) -> str:
    return _fernet().encrypt(json.dumps(dataclasses.asdict(pending)).encode()).decode()


def read_pending(value: str | None) -> PendingOidc | None:
    if not value:
        return None
    try:
        return PendingOidc(**json.loads(_fernet().decrypt(value.encode(), ttl=TTL_SECONDS)))
    except (InvalidToken, ValueError, TypeError):
        return None


def ticket_for(pending: PendingOidc) -> str:
    return _fernet().encrypt(json.dumps({"passed": _digest(pending)}).encode()).decode()


def ticket_matches(ticket: str, pending: PendingOidc) -> bool:
    try:
        data = json.loads(_fernet().decrypt(ticket.encode(), ttl=TTL_SECONDS))
    except (InvalidToken, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("passed") == _digest(pending)
```

If `_fernet` is private by convention, add a public `def fernet() -> MultiFernet: return _fernet()` in `app/services/crypto/secrets.py` and use that.

- [ ] **Step 4: Org admin routes** (in `app/routers/organizations.py`; reuse that file's existing `require_org_admin` / `get_current_user` imports)

```python
class OidcSettingsRequest(BaseModel):
    issuer: str = Field(max_length=2048)
    client_id: str = Field(min_length=1, max_length=255)
    # Left out: keep the stored secret (same issuer and client only).
    client_secret: str | None = Field(default=None, min_length=1, max_length=1024)


class OidcSaveRequest(OidcSettingsRequest):
    ticket: str = Field(max_length=4096)


def _sign_in_out(org: Organization) -> dict:
    return {
        "provider": org.sign_in_provider,
        "slug": org.slug,
        "login_link": f"{settings.public_base_url}/login/{org.slug}",
        "redirect_uri": settings.oidc_redirect_uri,
        "google_workspace_domain": org.google_workspace_domain,
        "oidc": (
            {"issuer": org.oidc_issuer, "client_id": org.oidc_client_id, "secret_set": bool(org.oidc_client_secret)}
            if org.oidc_issuer else None
        ),
    }


def _pending(org: Organization, body: OidcSettingsRequest) -> PendingOidc:
    try:
        issuer = oidc.normalize_issuer(body.issuer, allow_private=settings.oidc_allow_private_issuers)
    except oidc.OidcError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    secret = body.client_secret
    if secret is None:
        if org.oidc_issuer == issuer and org.oidc_client_id == body.client_id and org.oidc_client_secret:
            secret = org.oidc_client_secret
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "enter the client secret")
    return PendingOidc(org_id=str(org.id), issuer=issuer, client_id=body.client_id.strip(), client_secret=secret)


def _refuse_other_provider(org: Organization) -> None:
    if org.sign_in_provider in ("microsoft", "google"):
        raise HTTPException(status.HTTP_409_CONFLICT, "this organization signs in with " + org.sign_in_provider.title() + "; ask the platform admin to change it")


@router.get("/current/sign-in")
async def get_sign_in(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return _sign_in_out(await get_organization(db, user.organization_id))


@router.post("/current/sign-in/oidc/test")
async def start_oidc_test(
    body: OidcSettingsRequest, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    org = await get_organization(db, user.organization_id)
    _refuse_other_provider(org)
    pending = _pending(org, body)
    try:
        await oidc.discover(oidc.Provider(pending.issuer, pending.client_id, pending.client_secret, settings.oidc_allow_private_issuers))
    except oidc.OidcError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Couldn't use this provider: {exc}") from exc
    response.set_cookie(oidc_test.COOKIE, oidc_test.pending_cookie(pending), **{**cookie_kwargs(), "max_age": oidc_test.TTL_SECONDS})
    return {"start_url": "/api/auth/oidc/start-test"}


@router.put("/current/sign-in/oidc")
async def save_oidc(body: OidcSaveRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)) -> dict:
    org = await get_organization(db, user.organization_id)
    _refuse_other_provider(org)
    pending = _pending(org, body)
    if not oidc_test.ticket_matches(body.ticket, pending):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "run Test sign-in first: settings are only saved after a successful test")
    org.oidc_issuer, org.oidc_client_id, org.oidc_client_secret = pending.issuer, pending.client_id, pending.client_secret
    await db.commit()
    return _sign_in_out(org)


@router.delete("/current/sign-in/oidc")
async def remove_oidc(db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)) -> dict:
    org = await get_organization(db, user.organization_id)
    org.oidc_issuer = org.oidc_client_id = org.oidc_client_secret = None
    await db.commit()
    return _sign_in_out(org)


async def finish_oidc_test(request: Request, db: AsyncSession, cookies) -> HTMLResponse:
    """The end of a test sign-in: everything a real sign-in checks, except
    starting a session. Reports back to the Settings window."""
    from app.routers.auth_providers import _checked_claims, email_on_domains

    pending = oidc_test.read_pending(request.cookies.get(oidc_test.COOKIE))
    result: dict
    if pending is None or pending.org_id != cookies.org_id:
        result = {"ok": False, "error": "The test took too long or was started elsewhere. Run it again."}
    else:
        provider = oidc.Provider(pending.issuer, pending.client_id, pending.client_secret, settings.oidc_allow_private_issuers)
        try:
            claims = await _checked_claims(provider, request, cookies, settings.oidc_redirect_uri)
            email = str(claims.get("email") or "")
            await set_org_context(db, uuid.UUID(pending.org_id))
            if not email or claims.get("email_verified") is False:
                result = {"ok": False, "error": "The provider didn't send a verified email address."}
            elif not email_on_domains(email, await verified_domain_names(db, uuid.UUID(pending.org_id))):
                result = {"ok": False, "error": f"{email} isn't on one of your verified domains."}
            else:
                result = {"ok": True, "ticket": oidc_test.ticket_for(pending), "email": email}
        except (oidc.OidcError, KeyError) as exc:
            result = {"ok": False, "error": f"The provider's answer didn't check out: {exc}"}
    payload = json.dumps({"type": "yadt-oidc-test", **result})
    origin = json.dumps(settings.public_base_url)
    html = (
        "<!doctype html><meta charset=utf-8><title>Test sign-in</title>"
        f"<script>window.opener&&window.opener.postMessage({payload},{origin});window.close()</script>"
        "<p>You can close this window.</p>"
    )
    response = HTMLResponse(html)
    for name in (*oauth_cookies.NAMES, oidc_test.COOKIE):
        response.delete_cookie(name, path="/")
    return response
```

Note the test asserts on `'"ok": true'` — `json.dumps` default separators produce `"ok": true`, matching.

`GET /api/auth/oidc/start-test` in `auth_providers.py`:

```python
@router.get("/oidc/start-test")
async def oidc_start_test(request: Request) -> RedirectResponse:
    from app.services.auth import oidc_test

    pending = oidc_test.read_pending(request.cookies.get(oidc_test.COOKIE))
    if pending is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the test expired; start it again from Settings")
    provider = oidc.Provider(pending.issuer, pending.client_id, pending.client_secret, settings.oidc_allow_private_issuers)
    try:
        return await _redirect_to_provider(provider, redirect_uri=settings.oidc_redirect_uri, login_hint=None, org_id=pending.org_id, mode="test")
    except oidc.OidcError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Couldn't use this provider: {exc}") from exc
```

Imports to add to `organizations.py`: `json`, `uuid`, `Field`, `Request`, `Response`, `HTMLResponse`, `settings`, `set_org_context`, `oidc`, `oidc_test`, `PendingOidc`, `oauth_cookies`, `cookie_kwargs`, `verified_domain_names`, `Organization`.

- [ ] **Step 5: Platform admin fields** — `schemas/platform_admin.py`:

```python
class OrganizationCreateRequest(BaseModel):
    name: str
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    entra_tenant_id: uuid.UUID | None = None
    google_workspace_domain: str | None = Field(default=None, max_length=253)
    oidc_issuer: str | None = Field(default=None, max_length=2048)
    oidc_client_id: str | None = Field(default=None, max_length=255)
    oidc_client_secret: str | None = Field(default=None, max_length=1024)
    expected_admin_email: EmailStr | None = None
```

Keep the existing `admin_email_with_microsoft` validator and extend it: an admin email is required when any provider is set ("with a sign-in provider, only they become org admin"). Add the same new optional fields to `OrganizationUpdateRequest`. In `platform_admin.py`'s create and update handlers: lowercase/strip `google_workspace_domain`; `oidc.normalize_issuer(..., allow_private=settings.oidc_allow_private_issuers)` for the issuer (400 on OidcError); OIDC fields all-or-nothing on create (400 `"enter issuer, client ID and secret"`); after applying, if more than one of `entra_tenant_id`, `google_workspace_domain`, `oidc_issuer` is set → 409 `"an organization signs in with one provider: clear the other first"`; slug: given → must be unique (409 `"that sign-in link name is taken"`), else `unique_slug(name, taken)` where `taken` is all existing slugs. A unique-violation on `google_workspace_domain` → 409 `"another organization already uses this Google Workspace"` (check with a query first rather than catching IntegrityError). In the update handler, `"field" in body.model_fields_set` decides whether to change a field (so `null` clears it). The admin org JSON gains `slug`, `sign_in_provider`, `google_workspace_domain`, `oidc_issuer`, `oidc_client_id`, `oidc_secret_set`.

- [ ] **Step 6: Local teammates everywhere** — in `app/routers/users.py` and `app/routers/platform_admin.py`, remove the checks that refuse creating a local user because the organization has `entra_tenant_id` (grep `entra_tenant_id` in both; keep any other use). Update tests asserting that refusal (grep `test_` names containing `entra` and `local` in `tests/routers/test_users.py` and `test_platform_admin.py`) to assert creation now succeeds.

- [ ] **Step 7: Run — expect PASS**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_sign_in_settings.py tests/routers/test_platform_admin.py tests/routers/test_users.py tests/routers/test_auth_providers.py`

- [ ] **Step 8: Commit**

```bash
git add backend/app backend/tests
git commit -m "Sign-in settings: org admins test and save their OIDC provider; platform admin sets any provider"
```

---

### Task 7: Login page and direct link (frontend)

**Files:**
- Create: `frontend/src/pages/LoginOrg.tsx`, `frontend/src/pages/Login.test.tsx` (if absent; else extend)
- Modify: `frontend/src/pages/Login.tsx`, `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `GET /api/auth/config` (`entra_sso_enabled`, `google_sso_enabled`, `company_sign_in_enabled`), `POST /api/auth/discover`, `GET /api/auth/org/{slug}`.
- Produces: route `/login/:slug`; Login phases `"start" | "password" | "otp" | "enroll" | "recovery"` (`"entra"` renamed `"start"`).

- [ ] **Step 1: Failing test**

```tsx
// frontend/src/pages/Login.test.tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Login from "./Login";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
const getMock = vi.mocked(api.get);
const postMock = vi.mocked(api.post);

beforeEach(() => {
  getMock.mockReset().mockResolvedValue({ entra_sso_enabled: true, google_sso_enabled: true, company_sign_in_enabled: true });
  postMock.mockReset();
});

describe("Login", () => {
  it("offers Microsoft, Google, a company account and a password", async () => {
    renderWithAppProviders(<Login />);
    expect(await screen.findByText("Sign in with Microsoft")).toBeInTheDocument();
    expect(screen.getByText("Sign in with Google")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("you@company.com")).toBeInTheDocument();
    expect(screen.getByText(/email and password instead/)).toBeInTheDocument();
  });

  it("says so when an email has no company sign-in", async () => {
    postMock.mockResolvedValueOnce({ url: null });
    renderWithAppProviders(<Login />);
    fireEvent.change(await screen.findByPlaceholderText("you@company.com"), { target: { value: "jan@unknown.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue with company account" }));
    expect(await screen.findByText(/no company sign-in for this email address/)).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/auth/discover", { email: "jan@unknown.example" });
  });

  it("goes straight to email and password when the server has no providers", async () => {
    getMock.mockResolvedValue({ entra_sso_enabled: false, google_sso_enabled: false, company_sign_in_enabled: false });
    renderWithAppProviders(<Login />);
    await waitFor(() => expect(screen.getByPlaceholderText("Password")).toBeInTheDocument());
  });
});
```

If `renderWithAppProviders` lacks a router and Login uses `useSearchParams`, wrap with `MemoryRouter` the way other page tests do (check `src/test/render.tsx`).

- [ ] **Step 2: Run — expect FAIL.**

Run: `npx vitest run --maxWorkers=1 src/pages/Login.test.tsx`

- [ ] **Step 3: Implement Login changes**

In `Login.tsx`:
- Config type: `{ entra_sso_enabled: boolean; google_sso_enabled: boolean; company_sign_in_enabled: boolean }`; store as `config`. If none of the three is true → `setPhase("password")`.
- Rename phase `"entra"` to `"start"`; the start screen renders, in order: the error alert (existing), the Microsoft link (when `entra_sso_enabled`, unchanged), a Google link (when `google_sso_enabled`) `<a href="/api/auth/google/login" className="btn btn--secondary" style={{ width: "100%", marginTop: "0.6rem", padding: "0.65rem" }}><GoogleMark />Sign in with Google</a>`, then when `company_sign_in_enabled` a form:

```tsx
<form onSubmit={handleCompanySubmit} className="auth-form" style={{ marginTop: "1rem" }}>
  <label className="field-label">
    Company account
    <input className="input" type="email" placeholder="you@company.com" value={companyEmail}
      onChange={(e) => setCompanyEmail(e.target.value)} required />
  </label>
  {companyError && <div className="alert alert--warning" style={{ margin: 0 }}>{companyError}</div>}
  <button type="submit" className="btn btn--secondary" disabled={submitting}>Continue with company account</button>
</form>
```

with

```tsx
async function handleCompanySubmit(e: FormEvent) {
  e.preventDefault();
  setCompanyError(null);
  setSubmitting(true);
  try {
    const { url } = await api.post<{ url: string | null }>("/auth/discover", { email: companyEmail.trim() });
    if (url) window.location.assign(url);
    else setCompanyError("There's no company sign-in for this email address. Use one of the other options.");
  } catch (err) {
    setCompanyError(err instanceof ApiError ? err.message : "Couldn't check this address. Try again.");
  } finally {
    setSubmitting(false);
  }
}
```

- `GoogleMark`: a small inline SVG like `MicrosoftMark` (the four-colour "G"; 16×16, `aria-hidden`).
- `ERROR_MESSAGES` gains: `email_not_verified: "Your provider didn't confirm your email address."`, `hd_mismatch: "Use your company Google Workspace account, not a personal Google account."`, `email_domain_not_verified: "Your email address isn't on one of your organization's verified domains."`, `provider_unavailable: "Your organization's sign-in provider can't be reached right now."`; and `token_invalid` becomes `"Your provider's response could not be verified. Please try again."` (not Microsoft-specific any more).
- The "Back" button in the password phase shows when any provider option exists and goes to `"start"`.

- [ ] **Step 4: Direct link page**

```tsx
// frontend/src/pages/LoginOrg.tsx
import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { api } from "../api/client";

// /login/<slug>: straight to that organization's sign-in provider.
export default function LoginOrg() {
  const { slug = "" } = useParams();
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    api
      .get<{ url: string }>(`/auth/org/${encodeURIComponent(slug)}`)
      .then(({ url }) => window.location.replace(url))
      .catch(() => setMissing(true));
  }, [slug]);

  if (missing) return <Navigate to="/login?error=organization_not_provisioned" replace />;
  return <p className="muted" style={{ padding: "2rem", textAlign: "center" }}>Taking you to your sign-in page…</p>;
}
```

`App.tsx`: `<Route path="/login/:slug" element={<LoginOrg />} />` next to `/login`.

- [ ] **Step 5: Run — expect PASS**, plus `npx tsc -b`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "Login page: Google, company account by email, and direct organization links"
```

---

### Task 8: Settings → Sign-in (org admin) and admin organization fields (frontend)

**Files:**
- Create: `frontend/src/hooks/useSignInSettings.ts`, `frontend/src/pages/settings/SignInTab.tsx`, `frontend/src/pages/settings/SignInTab.test.tsx`
- Modify: `frontend/src/pages/SettingsLayout.tsx`, `frontend/src/App.tsx`, `frontend/src/api/types.ts`, `frontend/src/pages/admin/AdminOrganizations.tsx`, `frontend/src/hooks/useAdmin.ts`, `frontend/src/pages/Team.tsx`

**Interfaces:**
- Consumes: Task 6 endpoints.
- Produces: `SignInSettings` type `{ provider: "none"|"microsoft"|"google"|"oidc"; slug: string; login_link: string; redirect_uri: string; google_workspace_domain: string | null; oidc: { issuer: string; client_id: string; secret_set: boolean } | null }`; hooks `useSignInSettings()`, `useStartOidcTest()`, `useSaveOidc()`, `useRemoveOidc()`; `Organization.sign_in_provider` in `types.ts`.

- [ ] **Step 1: Failing test**

```tsx
// frontend/src/pages/settings/SignInTab.test.tsx
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import SignInTab from "./SignInTab";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "org_admin" } }) }));

const NONE = {
  provider: "none", slug: "acme", login_link: "https://dmarc.example/login/acme",
  redirect_uri: "https://dmarc.example/api/auth/oidc/callback", google_workspace_domain: null, oidc: null,
};

beforeEach(() => {
  vi.mocked(api.get).mockReset().mockResolvedValue(NONE);
  vi.mocked(api.post).mockReset().mockResolvedValue({ start_url: "/api/auth/oidc/start-test" });
  vi.mocked(api.put).mockReset().mockResolvedValue({ ...NONE, provider: "oidc", oidc: { issuer: "https://idp.acme.example", client_id: "yadt", secret_set: true } });
  vi.spyOn(window, "open").mockReturnValue(null);
});

describe("SignInTab", () => {
  it("only saves after a passed test, with the ticket it returned", async () => {
    renderWithAppProviders(<SignInTab />);
    expect(await screen.findByText("https://dmarc.example/api/auth/oidc/callback")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Issuer URL"), { target: { value: "https://idp.acme.example" } });
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "yadt" } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "s3cret" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Test sign-in" }));
    await waitFor(() => expect(window.open).toHaveBeenCalledWith("/api/auth/oidc/start-test", "yadt-oidc-test", expect.any(String)));

    act(() => {
      window.dispatchEvent(new MessageEvent("message", { origin: window.location.origin, data: { type: "yadt-oidc-test", ok: true, ticket: "t1", email: "jan@acme.example" } }));
    });
    expect(await screen.findByText(/Signed in as jan@acme.example/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith("/organizations/current/sign-in/oidc", expect.objectContaining({ ticket: "t1", issuer: "https://idp.acme.example" })));
  });

  it("shows a Microsoft org's setting as read-only", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...NONE, provider: "microsoft" });
    renderWithAppProviders(<SignInTab />);
    expect(await screen.findByText(/signs in with Microsoft/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Issuer URL")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Hooks**

```ts
// frontend/src/hooks/useSignInSettings.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export interface SignInSettings {
  provider: "none" | "microsoft" | "google" | "oidc";
  slug: string;
  login_link: string;
  redirect_uri: string;
  google_workspace_domain: string | null;
  oidc: { issuer: string; client_id: string; secret_set: boolean } | null;
}

export interface OidcSettingsIn {
  issuer: string;
  client_id: string;
  client_secret?: string;
}

const KEY = ["sign-in-settings"];

export function useSignInSettings() {
  return useQuery({ queryKey: KEY, queryFn: () => api.get<SignInSettings>("/organizations/current/sign-in") });
}

export function useStartOidcTest() {
  return useMutation({
    mutationFn: (body: OidcSettingsIn) => api.post<{ start_url: string }>("/organizations/current/sign-in/oidc/test", body),
  });
}

export function useSaveOidc() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OidcSettingsIn & { ticket: string }) => api.put<SignInSettings>("/organizations/current/sign-in/oidc", body),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}

export function useRemoveOidc() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<SignInSettings>("/organizations/current/sign-in/oidc"),
    onSuccess: (data) => qc.setQueryData(KEY, data),
  });
}
```

If the project keeps query keys in `hooks/queryKeys.ts`, add `signInSettings` there and use it.

- [ ] **Step 4: The tab**

```tsx
// frontend/src/pages/settings/SignInTab.tsx
import { useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import { useRemoveOidc, useSaveOidc, useSignInSettings, useStartOidcTest } from "../../hooks/useSignInSettings";

const PROVIDER_NAME = { microsoft: "Microsoft", google: "Google Workspace" } as const;

// How people in this organization sign in. Local accounts always work; on
// top of that one provider. Org admins manage their own OIDC provider here;
// Microsoft and Google are set by the platform admin.
export default function SignInTab() {
  const { data } = useSignInSettings();
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const startTest = useStartOidcTest();
  const save = useSaveOidc();
  const remove = useRemoveOidc();

  useEffect(() => {
    if (data?.oidc) {
      setIssuer(data.oidc.issuer);
      setClientId(data.oidc.client_id);
    }
  }, [data?.oidc]);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.data?.type !== "yadt-oidc-test") return;
      if (event.data.ok) {
        setTicket(event.data.ticket);
        setError(null);
        setResult(`Test passed. Signed in as ${event.data.email}. You can save now.`);
      } else {
        setTicket(null);
        setResult(null);
        setError(event.data.error ?? "The test sign-in failed.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  if (!data) return <p className="muted">Loading…</p>;

  const body = { issuer: issuer.trim(), client_id: clientId.trim(), ...(secret ? { client_secret: secret } : {}) };
  const message = (err: Error, fallback: string) => (err instanceof ApiError ? err.message : fallback);
  // Any change after a test needs a new test.
  const edit = (set: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    set(e.target.value);
    setTicket(null);
    setResult(null);
  };

  return (
    <div style={{ display: "grid", gap: "1rem", maxWidth: "40rem" }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Direct sign-in link</h2>
        <p className="section-hint">Share or bookmark this: it goes straight to your organization's sign-in.</p>
        <code>{data.login_link}</code>
      </div>

      <div className="card">
        <h2 style={{ marginTop: 0 }}>Sign-in provider</h2>
        {data.provider === "microsoft" || data.provider === "google" ? (
          <p className="section-hint" style={{ margin: 0 }}>
            Your organization signs in with {PROVIDER_NAME[data.provider]}
            {data.provider === "google" && data.google_workspace_domain ? ` (${data.google_workspace_domain})` : ""}. Ask the
            platform admin to change this. Local accounts keep working next to it.
          </p>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!ticket) return;
              setError(null);
              save.mutate({ ...body, ticket }, { onSuccess: () => setResult("Saved. People can now sign in with your provider."), onError: (err) => setError(message(err, "couldn't save")) });
            }}
            style={{ display: "grid", gap: "0.6rem" }}
          >
            <p className="section-hint" style={{ margin: 0 }}>
              Any OpenID Connect provider works (Authentik, Keycloak, Zitadel, Okta, ...). Register this app there with
              the redirect URI below; people sign in with an email address on one of your verified domains.
            </p>
            <div>
              Redirect URI: <code>{data.redirect_uri}</code>
            </div>
            <label className="field-label">
              Issuer URL
              <input className="input" value={issuer} onChange={edit(setIssuer)} placeholder="https://auth.example.com/application/o/yadt" required />
            </label>
            <label className="field-label">
              Client ID
              <input className="input" value={clientId} onChange={edit(setClientId)} required />
            </label>
            <label className="field-label">
              Client secret
              <input className="input" type="password" value={secret} onChange={edit(setSecret)} placeholder={data.oidc?.secret_set ? "unchanged" : ""} required={!data.oidc?.secret_set} autoComplete="new-password" />
            </label>
            {result && <div className="alert alert--good" style={{ margin: 0 }}>{result}</div>}
            {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
            <div className="chip-row">
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                disabled={startTest.isPending || !issuer || !clientId}
                onClick={() => {
                  setError(null);
                  setResult(null);
                  startTest.mutate(body, {
                    onSuccess: ({ start_url }) => window.open(start_url, "yadt-oidc-test", "width=520,height=680"),
                    onError: (err) => setError(message(err, "couldn't start the test")),
                  });
                }}
              >
                Test sign-in
              </button>
              <button type="submit" className="btn btn--primary btn--sm" disabled={!ticket || save.isPending}>
                Save
              </button>
              {data.oidc && (
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => remove.mutate()} disabled={remove.isPending}>
                  Remove provider
                </button>
              )}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
```

`SettingsLayout.tsx`: add `{ to: "sign-in", label: "Sign-in" }` to the admin-only tabs, before "Sign-in activity". `App.tsx`: `<Route path="sign-in" element={<SignInTab />} />` under `/settings`.

- [ ] **Step 5: Admin organization form** — in `AdminOrganizations.tsx` org detail (where `tenantId` is edited, around the existing `entra_tenant_id` field), add a "Sign-in provider" select (`None`, `Microsoft`, `Google Workspace`, `Other OIDC`) driving which inputs show: Microsoft → tenant ID (existing field); Google → "Workspace domain"; OIDC → issuer, client ID, client secret (placeholder "unchanged" when `oidc_secret_set`). Save sends the chosen provider's fields and `null` for the other two providers' fields, via the existing `useUpdateAdminOrganization`. Show `slug` with the full link `/login/<slug>` and an edit field. Extend `AdminOrganization` in `useAdmin.ts` with `slug`, `sign_in_provider`, `google_workspace_domain`, `oidc_issuer`, `oidc_client_id`, `oidc_secret_set`. Show `{!org.entra_tenant_id && <CreateLocalUser .../>}` → always `<CreateLocalUser .../>`. Add a vitest case in the existing `AdminOrganizations.test.tsx` that choosing "Google Workspace" and entering a domain PATCHes `{ google_workspace_domain: "acme.example", entra_tenant_id: null, oidc_issuer: null, oidc_client_id: null, oidc_client_secret: null }`.

- [ ] **Step 6: Team** — `Team.tsx` line `{org && (org.entra_tenant_id ? <ShareSignInLink /> : canManage ? <AddLocalTeammate /> : null)}` becomes:

```tsx
{org && org.sign_in_provider !== "none" && <ShareSignInLink />}
{canManage && <AddLocalTeammate />}
```

and `ShareSignInLink` shows the org's `login_link` (from `useSignInSettings`) instead of the bare login URL if it currently shows one. Add `sign_in_provider: "none" | "microsoft" | "google" | "oidc"` to `Organization` in `types.ts`, and return it from the backend's current-organization JSON (`app/routers/organizations.py`, the function building the current org response) — add a backend assertion for it in an existing organizations test.

- [ ] **Step 7: Run frontend checks — expect PASS**

Run: `npx tsc -b && npx vitest run --maxWorkers=1 src/pages/settings src/pages/admin src/pages/Team src/pages/Login`

- [ ] **Step 8: Commit**

```bash
git add frontend/src backend/app backend/tests
git commit -m "Settings → Sign-in: test and save an OIDC provider; admin sets any provider; local teammates everywhere"
```

---

### Task 9: Verification, docs, release

**Files:**
- Modify: `.env.example` (repo root; and any `deploy/` env templates that list `ENTRA_SSO_CLIENT_ID` — grep), wiki pages in the scratchpad wiki clone (not the repo).

- [ ] **Step 1: Full backend suite and frontend suite** (one after the other, never together)

Run: backend `~/.local/bin/pytest -q -p no:cacheprovider` (expect all pass); frontend `npx tsc -b && npx vitest run --maxWorkers=1`.

- [ ] **Step 2: Live check against a local Keycloak**

Start Keycloak (exact pinned image, e.g. `quay.io/keycloak/keycloak:26.0.7`) on the host with a realm `acme`, client `yadt` (confidential, redirect `http://127.0.0.1:8000/api/auth/oidc/callback`), a user `jan@acme.example` with verified email. Run the API from the worktree (uvicorn on 127.0.0.1:8000 against a scratch database migrated to head, `OIDC_ALLOW_PRIVATE_ISSUERS=true`, `PUBLIC_BASE_URL=http://127.0.0.1:8000`), the frontend with vite proxying `/api` to it. In Playwright: org admin opens Settings → Sign-in, runs Test sign-in (popup logs in at Keycloak), saves; signs out; on the login page enters `jan@acme.example` under company account → lands on the dashboard as that user; `/login/<slug>` goes straight to Keycloak. Then remove the Keycloak container, the scratch database and any temp config.

- [ ] **Step 3: Docs**

`.env.example`: add, next to the Entra SSO block:

```
# Sign in with Google Workspace (optional). Google Cloud console → APIs & Services
# → Credentials → OAuth client ID (Web application); redirect URI:
# <PUBLIC_BASE_URL>/api/auth/google/callback
GOOGLE_SSO_CLIENT_ID=
GOOGLE_SSO_CLIENT_SECRET=
# Organizations' own sign-in providers on a private address (10.x, 192.168.x)
# are refused unless this is true. Only for servers where every organization is trusted.
OIDC_ALLOW_PRIVATE_ISSUERS=false
```

Wiki: new page "Sign-in Providers" (Google setup in Google Cloud; OIDC with Authentik and Keycloak examples: redirect URI, scopes `openid email profile`, email must be verified and on a verified domain; direct links; local accounts keep working); update "Configuration Reference" with the three env vars; Roadmap: mark the sign-in part of v0.3.0 as in beta.

- [ ] **Step 4: Commit, push, CI, beta**

```bash
git add .env.example
git commit -m "Docs: Google and OIDC sign-in settings"
git push origin HEAD:v0.3.0-beta
```

Wait for PR #41 checks (backend-tests, frontend-build, CodeQL) to pass, then tag `v0.3.0-beta11` with release notes covering: Google sign-in, own OIDC provider, company-account login and direct links, local accounts in every organization, the Team page fix (leftover "Domains" link removed), the new env vars, and "migration 0043 runs automatically".
