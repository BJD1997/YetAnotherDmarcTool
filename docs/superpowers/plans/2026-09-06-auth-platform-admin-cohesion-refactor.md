# Auth & Platform-Admin Cohesion Refactor (Plan A2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a schema layer and a repository/query layer for `auth.py` and `platform_admin.py` — the two routers deferred from the merged "Plan A" backend cohesion refactor — with HTTP-level integration tests covering their session/MFA/TOTP flows, and dedupe the last remaining copy of `_get_owned_domain` (in a router not touched by this plan, `dmarc_reports.py`, is intentionally NOT covered here — see Not in this plan).

**Architecture:** Same pattern as the merged Plan A: for each router, write integration tests against the *current* code first (characterization, not red/green TDD), then extract Pydantic models into `app/schemas/<name>.py` and query logic into `app/repositories/<name>.py`, then re-run the same tests to prove behavior didn't change. Two new repository files are needed (`repositories/auth.py`, `repositories/platform_admin.py`) since neither router's queries fit an existing one; both routers reuse `repositories/organizations.py::get_organization` and `repositories/mailbox_connections.py::get_org_mailbox_connection` where their inline queries are identical to functions Plan A already created.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Postgres, pytest + pytest-asyncio (`asyncio_mode = auto`), httpx (`AsyncClient` + `ASGITransport`), `pyotp` (already a pinned dependency, used directly in tests to compute valid TOTP codes), `monkeypatch` (pytest built-in, used to stub the two Microsoft-calling functions in the OAuth callback tests — no new dependency).

**Spec:** `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`

**Not in this plan (follow-up "Plan A3"):** `dmarc_reports.py` — the largest, most analytically complex router in the app (1198 lines, the core reporting/analytics surface), deliberately handled as its own plan. Its local `_get_owned_domain` copy is untouched here; Plan A3 will dedupe it onto `app.repositories.domains.get_owned_domain`, the same function this plan's routers also use.

## Global Constraints

- Work happens on the current branch (`worktree-plan-a2-auth-platform-admin`, cut from `master` post-Plan-A-merge) in its own worktree.
- Every function in `app/repositories/*.py` takes `db: AsyncSession` as its first argument and returns data or raises `HTTPException` for a not-found/ownership check — no module-level caches, singletons, or other process-local state (required for horizontal scale-out; see spec).
- `pytest -v` (run from `backend/`, with `TEST_DATABASE_URL` set) must be fully green before every commit.
- No behavior changes: a test that passes against the pre-refactor router must still pass, unmodified, after the schema/repository extraction for that router.
- Match existing code style: type hints everywhere, `datetime.now(timezone.utc)` (never naive `datetime.now()`), no comments unless something is genuinely non-obvious.
- **Critical, project-specific test-infrastructure fact:** `app/services/auth/rate_limit.py`'s `login_limiter` and `otp_limiter` are module-level in-memory singletons (`max_events=10, window_seconds=300`), shared across the entire pytest process. `local_login`, `verify_otp` (auth.py) and `admin_login`, `admin_verify_otp` (platform_admin.py) are ALL rate-limited by these same two shared limiters (`login_limiter` covers both login endpoints, `otp_limiter` covers both OTP-verify endpoints). Without a reset between tests, the 11th test in a session that hits any of these four endpoints from the same simulated client IP gets a spurious 429. Task 1 fixes this once, in the shared `api` fixture, before any auth-flow tests are written.
- **Project-specific TOTP test fact:** to produce a code that `totp.verify_code(secret, code)` will accept in a test, call `pyotp.TOTP(secret).now()` directly — `pyotp` is already a pinned dependency (used by `app/services/auth/totp.py` itself), no new import needed beyond `import pyotp` in the test file.
- **Project-specific OAuth test fact:** `app/services/auth/entra_oidc.py`'s `exchange_code_for_tokens`/`validate_id_token` make real HTTPS calls to `login.microsoftonline.com`. `auth.py` imports the `entra_oidc` *module* (`from app.services.auth import entra_oidc, pkce, session_manager, totp`), not the individual function names — so `monkeypatch.setattr(entra_oidc, "exchange_code_for_tokens", ...)` correctly affects `auth.py`'s calls (unlike the `async_session_factory` direct-import gotcha Plan A hit with `app/main.py` — that doesn't apply here since this is a module import, not a `from X import name` import).

---

### Task 1: Test infrastructure (rate-limiter reset) + `auth.py` schema/repository extraction (reference implementation)

**Files:**
- Modify: `backend/tests/conftest.py`
- Modify: `backend/app/routers/auth.py`
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/repositories/auth.py`
- Create: `backend/tests/routers/test_auth.py`

**Interfaces:**
- Consumes: `api` fixture, `seed_org_and_user`, `login_as` (already exist in `conftest.py` from Plan A).
- Produces (used by Task 2): `app.repositories.auth.get_organization_by_entra_tenant_id`, `get_user_by_org_and_entra_object_id`, `count_users_for_org`, `get_local_user_by_email`, `get_mfa_pending_challenge`, `get_unused_recovery_code`, `get_password_setup_token` — all `(db: AsyncSession, ...) -> Model | None` or `-> int`.

- [ ] **Step 1: Add the rate-limiter reset to the shared `api` fixture**

In `backend/tests/conftest.py`, add this import near the top (alongside the existing `app.middleware.demo_read_only`/`app.workers.jobs.mailbox_poll_job` imports):

```python
from app.services.auth.rate_limit import login_limiter, otp_limiter
```

Then, as the FIRST lines inside the `api` fixture's body (before the `TRUNCATE organizations CASCADE` line), add:

```python
    login_limiter._hits.clear()
    otp_limiter._hits.clear()
```

Update the fixture's docstring to add one sentence: "Also clears the shared `login_limiter`/`otp_limiter` in-memory rate-limit state at the start of every test — both are module-level singletons (see `rate_limit.py`), so without this reset the 11th test in a session hitting any rate-limited auth endpoint from the same simulated client IP gets a spurious 429."

- [ ] **Step 2: Write `test_auth.py`'s simple-endpoint tests against the current router**

```python
from tests.conftest import login_as, seed_org_and_user


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
```

Note: `test_auth_config_sso_disabled` and `test_login_redirect_404_when_sso_disabled` assert on the SSO-disabled path because the test environment has no real `ENTRA_SSO_CLIENT_ID` configured — this is itself a correct characterization of this test environment's config, not a gap. The SSO-enabled path (the `/auth/callback` OAuth flow) is covered separately in Task 3 with `entra_sso_client_id` patched on.

- [ ] **Step 2b: Run tests, confirm pass against the current router**

Run: `cd backend && pytest tests/routers/test_auth.py -v`
Expected: 5 passed.

- [ ] **Step 3: Extract the schema**

Create `backend/app/schemas/auth.py`:

```python
from pydantic import BaseModel


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class VerifyOtpRequest(BaseModel):
    code: str


class SetPasswordRequest(BaseModel):
    token: str
    new_password: str


class EnrollOtpConfirmRequest(BaseModel):
    secret: str
    code: str
```

- [ ] **Step 4: Extract the repository**

Create `backend/app/repositories/auth.py`:

```python
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuthMethod
from app.models.mfa_pending_challenge import MfaPendingChallenge
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.user import User
from app.models.user_recovery_code import UserRecoveryCode


async def get_organization_by_entra_tenant_id(db: AsyncSession, tenant_id: str) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.entra_tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def get_user_by_org_and_entra_object_id(db: AsyncSession, organization_id: UUID, object_id: str) -> User | None:
    result = await db.execute(
        select(User).where(User.organization_id == organization_id, User.entra_object_id == object_id)
    )
    return result.scalar_one_or_none()


async def count_users_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(select(func.count()).select_from(User).where(User.organization_id == organization_id))
    return result.scalar_one()


async def get_local_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower(), User.auth_method == AuthMethod.local)
    )
    return result.scalar_one_or_none()


async def get_mfa_pending_challenge(db: AsyncSession, token_hash: str) -> MfaPendingChallenge | None:
    result = await db.execute(select(MfaPendingChallenge).where(MfaPendingChallenge.token_hash == token_hash))
    return result.scalar_one_or_none()


async def get_unused_recovery_code(db: AsyncSession, user_id: UUID, code_hash: str) -> UserRecoveryCode | None:
    result = await db.execute(
        select(UserRecoveryCode).where(
            UserRecoveryCode.user_id == user_id,
            UserRecoveryCode.code_hash == code_hash,
            UserRecoveryCode.used_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_password_setup_token(db: AsyncSession, token_hash: str) -> PasswordSetupToken | None:
    result = await db.execute(select(PasswordSetupToken).where(PasswordSetupToken.token_hash == token_hash))
    return result.scalar_one_or_none()
```

- [ ] **Step 5: Update the router's imports only (Step 2's tests don't exercise the parts these serve — this task only wires up the imports the extracted pieces need; Task 2 will replace the actual inline queries in `local_login`/`verify_otp`/`set_password`, and Task 3 replaces `callback`'s)**

This step is two separate edits to `backend/app/routers/auth.py`, both mechanical (no function body touched):

**Edit A — replace the top-of-file import block** (from the first `import secrets` line down through `_MFA_PENDING_COOKIE = settings.mfa_pending_cookie_name`) with:

```python
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context, set_platform_admin_context
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.enums import AuthMethod, OrganizationStatus, SignInResult, UserRole, UserStatus
from app.models.mfa_pending_challenge import MfaPendingChallenge
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.user import User
from app.models.user_recovery_code import UserRecoveryCode
from app.repositories.auth import (
    count_users_for_org,
    get_local_user_by_email,
    get_mfa_pending_challenge,
    get_organization_by_entra_tenant_id,
    get_password_setup_token,
    get_unused_recovery_code,
    get_user_by_org_and_entra_object_id,
)
from app.repositories.organizations import get_organization
from app.schemas.auth import EnrollOtpConfirmRequest, LocalLoginRequest, SetPasswordRequest, VerifyOtpRequest
from app.services.auth import entra_oidc, pkce, session_manager, totp
from app.services.auth.password import dummy_verify, hash_password, verify_password
from app.services.auth.rate_limit import login_limiter, otp_limiter, rate_limiter
from app.services.auth.session_manager import cookie_kwargs
from app.services.auth.sign_in_log import client_network_info, record_sign_in_event
from app.services.auth.tokens import hash_token, new_opaque_token

router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_STATE_COOKIE = "oauth_state"
_OAUTH_VERIFIER_COOKIE = "oauth_verifier"
_MFA_PENDING_COOKIE = settings.mfa_pending_cookie_name
```

`pydantic.BaseModel` is dropped from the import list above since it's no longer used directly in this file after Edit B removes the last class that used it.

**Edit B — remove the 4 class definitions, further down in the file.** Find this block (it comes after `me()`, right below the "`---------- Local email+password+TOTP login ----------`" banner comment):

```python
class LocalLoginRequest(BaseModel):
    email: str
    password: str


class VerifyOtpRequest(BaseModel):
    code: str


class SetPasswordRequest(BaseModel):
    token: str
    new_password: str


class EnrollOtpConfirmRequest(BaseModel):
    secret: str
    code: str
```

Delete these 4 class definitions entirely. **Keep the banner comment directly above them** (the "`---------- Local email+password+TOTP login ----------`" block explaining the two-step password+TOTP design) — it's genuinely non-obvious router-level context, not part of what moved to `schemas/auth.py`. After this edit, that banner comment is immediately followed by `async def _set_mfa_pending(...)`.

Do not touch any function body in this step — that's Tasks 2 and 3. This step only changes imports/class-removal so the file still parses; the inline queries these repository functions will eventually replace are untouched here.

- [ ] **Step 6: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_auth.py -v`
Expected: 5 passed (no behavior touched yet — this step is a pure import/schema reshuffle).

- [ ] **Step 7: Commit**

```bash
git add backend/tests/conftest.py backend/app/schemas/auth.py backend/app/repositories/auth.py backend/app/routers/auth.py backend/tests/routers/test_auth.py
git commit -m "Add rate-limiter test reset; extract auth.py schema/repository layer (imports only)"
```

---

### Task 2: `auth.py` local login + TOTP + password-reset flow

**Files:**
- Modify: `backend/app/routers/auth.py`
- Modify: `backend/tests/routers/test_auth.py`

**Interfaces:**
- Consumes: everything Task 1 produced (`repositories/auth.py`'s functions, `schemas/auth.py`'s models), plus `repositories/organizations.py::get_organization` (Plan A).

- [ ] **Step 1: Add local-login/TOTP/password-reset tests to `test_auth.py`**

Append to `backend/tests/routers/test_auth.py`:

```python
import pyotp

from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.services.auth.password import hash_password


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
```

Note: `test_local_login_needs_enrollment_when_no_totp`'s cookie assertion is deliberately loose (`"dmarc_mfa_pending" in response.cookies or any(...)`) because the exact cookie name comes from `settings.mfa_pending_cookie_name`, which this test doesn't need to hardcode — the point is *a* pending-MFA cookie got set, not its exact name.

- [ ] **Step 2: Run the new tests, confirm they pass against the CURRENT (pre-repository-extraction) router bodies**

Run: `cd backend && pytest tests/routers/test_auth.py -v`
Expected: 12 passed (5 from Task 1 + 7 new).

- [ ] **Step 3: Replace the inline queries in `local_login`, `_get_pending_user`, `verify_otp`, `set_password` with the repository functions**

In `backend/app/routers/auth.py`:

1. In `local_login`, replace:
```python
    result = await db.execute(
        select(User).where(func.lower(User.email) == body.email.strip().lower(), User.auth_method == AuthMethod.local)
    )
    user = result.scalar_one_or_none()
```
with:
```python
    user = await get_local_user_by_email(db, body.email)
```
Also in `local_login`, replace `org = await db.get(Organization, user.organization_id)` with `org = await get_organization(db, user.organization_id)`.

2. In `_get_pending_user`, replace:
```python
    result = await db.execute(select(MfaPendingChallenge).where(MfaPendingChallenge.token_hash == token_hash))
    challenge = result.scalar_one_or_none()
```
with:
```python
    challenge = await get_mfa_pending_challenge(db, token_hash)
```

3. In `verify_otp`, replace:
```python
        code_hash = totp.hash_recovery_code_for_lookup(code)
        result = await db.execute(
            select(UserRecoveryCode).where(
                UserRecoveryCode.user_id == user.id,
                UserRecoveryCode.code_hash == code_hash,
                UserRecoveryCode.used_at.is_(None),
            )
        )
        recovery = result.scalar_one_or_none()
```
with:
```python
        code_hash = totp.hash_recovery_code_for_lookup(code)
        recovery = await get_unused_recovery_code(db, user.id, code_hash)
```

4. In `set_password`, replace:
```python
    result = await db.execute(select(PasswordSetupToken).where(PasswordSetupToken.token_hash == token_hash))
    setup_token = result.scalar_one_or_none()
```
with:
```python
    setup_token = await get_password_setup_token(db, token_hash)
```

5. Remove now-unused imports: `select` (check remaining usage in the file first — `callback` still uses `select` for `Organization`/`User` lookups until Task 3, so **do not remove `select` yet**), `func` (check — `callback` also uses `func.count()` until Task 3, **do not remove yet either**). `MfaPendingChallenge`, `PasswordSetupToken`, `UserRecoveryCode` model imports ARE safe to remove now (no longer referenced directly in this file after the above 4 changes — verify with a grep before removing).

- [ ] **Step 4: Run tests, confirm all still pass**

Run: `cd backend && pytest tests/routers/test_auth.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/auth.py backend/tests/routers/test_auth.py
git commit -m "Migrate auth.py local-login/TOTP/password-reset flow onto the repository layer"
```

---

### Task 3: `auth.py` Entra OAuth callback

**Files:**
- Modify: `backend/app/routers/auth.py`
- Modify: `backend/tests/routers/test_auth.py`

**Interfaces:**
- Consumes: `get_organization_by_entra_tenant_id`, `get_user_by_org_and_entra_object_id`, `count_users_for_org` (Task 1).

- [ ] **Step 1: Add OAuth callback tests to `test_auth.py`**

Append to `backend/tests/routers/test_auth.py`:

```python
async def _mock_entra_success(monkeypatch, *, tenant_id: str, object_id: str, email: str, name: str = "Test User"):
    async def _fake_exchange(**kwargs):
        return {"id_token": "fake-id-token", "access_token": "fake-access-token"}

    async def _fake_validate(id_token):
        return {"tid": tenant_id, "oid": object_id, "preferred_username": email, "name": name}

    monkeypatch.setattr("app.routers.auth.entra_oidc.exchange_code_for_tokens", _fake_exchange)
    monkeypatch.setattr("app.routers.auth.entra_oidc.validate_id_token", _fake_validate)


async def test_callback_creates_first_user_as_org_admin(api, monkeypatch):
    client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=True)
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
```

`monkeypatch` is a built-in pytest fixture (auto-available, no import needed beyond it appearing as a test parameter) that undoes the patch automatically at test end — no manual restore needed, unlike the module-level patches in `conftest.py`'s `api` fixture.

- [ ] **Step 2: Run tests, confirm they pass against the CURRENT (pre-repository-extraction) `callback` body**

Run: `cd backend && pytest tests/routers/test_auth.py -v`
Expected: 16 passed (12 from Task 2 + 4 new).

- [ ] **Step 3: Replace `callback`'s inline queries with the repository functions**

In `backend/app/routers/auth.py`'s `callback` function:

1. Replace:
```python
    result = await db.execute(select(Organization).where(Organization.entra_tenant_id == tenant_id))
    org = result.scalar_one_or_none()
```
with:
```python
    org = await get_organization_by_entra_tenant_id(db, tenant_id)
```

2. Replace:
```python
    result = await db.execute(
        select(User).where(User.organization_id == org.id, User.entra_object_id == object_id)
    )
    user = result.scalar_one_or_none()
```
with:
```python
    user = await get_user_by_org_and_entra_object_id(db, org.id, object_id)
```

3. Replace:
```python
        count_result = await db.execute(
            select(func.count()).select_from(User).where(User.organization_id == org.id)
        )
        is_first_user = count_result.scalar_one() == 0
```
with:
```python
        is_first_user = await count_users_for_org(db, org.id) == 0
```

4. Now remove the now-fully-unused imports: `from sqlalchemy import func, select` (verify nothing else in the file uses either — after Task 2's changes and these, nothing should), `from app.models.organization import Organization` (verify — `local_login`'s org lookup now goes through `get_organization`, and nothing else in this file constructs/queries `Organization` directly), `from app.models.user import User` (verify — check `me`'s return type hint `user: User = Depends(get_current_user)` still needs it! **Do not remove `User`** — it's still used as a type annotation in `me()`, `callback()`, `local_login()`, `verify_otp()`, `enroll_otp_confirm()`).

- [ ] **Step 4: Run tests, confirm all still pass**

Run: `cd backend && pytest tests/routers/test_auth.py -v`
Expected: 16 passed.

- [ ] **Step 5: Run the FULL suite** (this task removes imports used file-wide; a stale import removal would be a syntax/NameError, not just a test failure in this one file)

Run: `cd backend && pytest -v`
Expected: 192 (Plan A baseline) + 16 (this router's tests, replacing 0 since `auth.py` had none before) = 208 passed. Confirm no other file's tests regressed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/auth.py backend/tests/routers/test_auth.py
git commit -m "Migrate auth.py Entra OAuth callback onto the repository layer"
```

---

### Task 4: `platform_admin.py` schema/repository extraction + admin login/TOTP flow

**Files:**
- Modify: `backend/app/routers/platform_admin.py`
- Create: `backend/app/schemas/platform_admin.py`
- Create: `backend/app/repositories/platform_admin.py`
- Create: `backend/tests/routers/test_platform_admin.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `api`, `seed_org_and_user`, `login_as`, `login_as_platform_admin` (Plan A, Task 9) — **note: `login_as_platform_admin` logs in as an admin with NO TOTP enrolled**, which doesn't fit every test here; this task adds a second helper.
- Produces (used by Tasks 5-6): `app.repositories.platform_admin.get_platform_admin_by_email`, `get_admin_mfa_pending_challenge`, `get_unused_admin_recovery_code`, `list_all_organizations`, `org_aggregates`, `list_job_runs`, `job_runs_summary_stats` — plus reuse of `app.repositories.organizations.get_organization` and `app.repositories.mailbox_connections.get_org_mailbox_connection` (both from Plan A).

- [ ] **Step 1: Add a TOTP-enrolled platform-admin login helper to `conftest.py`**

`login_as_platform_admin` (from Plan A) creates an admin with NO TOTP enrolled and logs straight in via a session — it never exercises the MFA-pending flow at all, which this task's tests need to. Append to `backend/tests/conftest.py`:

```python
async def seed_platform_admin_with_totp(owner_factory) -> tuple:
    """Creates a local PlatformAdmin WITH a TOTP secret already enrolled
    (unlike login_as_platform_admin, which logs straight in with no MFA
    step at all) — returns (admin, secret) so tests can compute valid
    codes with pyotp.TOTP(secret).now()."""
    import pyotp

    from app.models.platform_admin import PlatformAdmin
    from app.services.auth.password import hash_password

    secret = pyotp.random_base32()
    async with owner_factory() as db:
        admin = PlatformAdmin(
            email=f"admin-totp+{uuid.uuid4()}@platform.example",
            password_hash=hash_password("correct horse battery staple"),
            is_active=True,
            otp_secret=secret,
            otp_enrolled_at=datetime.now(timezone.utc),
        )
        db.add(admin)
        await db.flush()
        await db.refresh(admin)
        await db.commit()
        return admin, secret
```

This needs `import uuid` and `from datetime import datetime, timezone` — both already imported at the top of `conftest.py` from Task 1's/Plan A's earlier work; verify and add if somehow missing.

- [ ] **Step 2: Write `test_platform_admin.py`'s admin-login/TOTP tests against the current router**

```python
import pyotp

from tests.conftest import login_as_platform_admin, seed_platform_admin_with_totp


async def test_admin_login_requires_credentials(api):
    client, _owner_factory = api
    response = await client.post("/api/admin/login", json={"email": "nobody@platform.example", "password": "wrong"})
    assert response.status_code == 401


async def test_admin_login_and_verify_otp_full_flow(api):
    client, owner_factory = api
    admin, secret = await seed_platform_admin_with_totp(owner_factory)

    login_response = await client.post(
        "/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"}
    )
    assert login_response.status_code == 200
    assert login_response.json() == {"needs_enrollment": False}

    verify_response = await client.post("/api/admin/verify-otp", json={"code": pyotp.TOTP(secret).now()})
    assert verify_response.status_code == 204

    me_response = await client.get("/api/admin/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == admin.email
    assert me_response.json()["auth_type"] == "local"


async def test_admin_verify_otp_rejects_wrong_code(api):
    client, owner_factory = api
    admin, _secret = await seed_platform_admin_with_totp(owner_factory)
    await client.post("/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"})

    response = await client.post("/api/admin/verify-otp", json={"code": "000000"})

    assert response.status_code == 401


async def test_admin_enroll_otp_flow(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)  # this helper's admin has NO TOTP enrolled yet — wrong tool here

    # login_as_platform_admin bypasses the MFA-pending step entirely (mints a
    # real session directly), so it can't be used to test enroll-otp, which
    # needs a pending-MFA cookie. Re-seed a fresh not-yet-enrolled admin and
    # drive it through /login for real instead.
    from app.services.auth.password import hash_password
    from app.models.platform_admin import PlatformAdmin

    async with owner_factory() as db:
        admin = PlatformAdmin(
            email="fresh-admin@platform.example", password_hash=hash_password("correct horse battery staple"), is_active=True
        )
        db.add(admin)
        await db.flush()
        await db.refresh(admin)
        await db.commit()

    login_response = await client.post(
        "/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"}
    )
    assert login_response.json() == {"needs_enrollment": True}

    enroll_response = await client.post("/api/admin/enroll-otp")
    assert enroll_response.status_code == 200
    secret = enroll_response.json()["secret"]

    confirm_response = await client.post(
        "/api/admin/enroll-otp/confirm", json={"secret": secret, "code": pyotp.TOTP(secret).now()}
    )
    assert confirm_response.status_code == 200
    assert len(confirm_response.json()["recovery_codes"]) == 10

    me_response = await client.get("/api/admin/me")
    assert me_response.status_code == 200


async def test_admin_logout(api):
    client, owner_factory = api
    admin, secret = await seed_platform_admin_with_totp(owner_factory)
    await client.post("/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"})
    await client.post("/api/admin/verify-otp", json={"code": pyotp.TOTP(secret).now()})

    logout_response = await client.post("/api/admin/logout")
    assert logout_response.status_code == 204

    me_response = await client.get("/api/admin/me")
    assert me_response.status_code == 401


async def test_change_password_requires_local_session(api):
    client, owner_factory = api
    admin, secret = await seed_platform_admin_with_totp(owner_factory)
    await client.post("/api/admin/login", json={"email": admin.email, "password": "correct horse battery staple"})
    await client.post("/api/admin/verify-otp", json={"code": pyotp.TOTP(secret).now()})

    response = await client.post(
        "/api/admin/change-password",
        json={"current_password": "correct horse battery staple", "new_password": "a brand new password entirely"},
    )

    assert response.status_code == 204
```

- [ ] **Step 3: Run tests, confirm pass against the current router**

Run: `cd backend && pytest tests/routers/test_platform_admin.py -v`
Expected: 6 passed.

- [ ] **Step 4: Extract the schema**

Create `backend/app/schemas/platform_admin.py`:

```python
import uuid

from pydantic import BaseModel, EmailStr, field_validator

from app.models.enums import ConsentStatus, OrganizationStatus, UserRole


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class AdminVerifyOtpRequest(BaseModel):
    code: str


class AdminEnrollOtpConfirmRequest(BaseModel):
    secret: str
    code: str


class OrganizationCreateRequest(BaseModel):
    name: str
    entra_tenant_id: uuid.UUID | None = None


class OrganizationUpdateRequest(BaseModel):
    name: str | None = None
    entra_tenant_id: uuid.UUID | None = None
    status: OrganizationStatus | None = None


class MailboxConnectionRequest(BaseModel):
    mailbox_address: str
    consent_status: ConsentStatus | None = None


class LocalUserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None
    role: UserRole = UserRole.org_admin


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError("new password must be at least 12 characters")
        return value
```

- [ ] **Step 5: Extract the repository (this task's slice — admin auth queries only; org/job-run queries come in Tasks 5-6)**

Create `backend/app/repositories/platform_admin.py`:

```python
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_admin import PlatformAdmin
from app.models.platform_admin_mfa_pending_challenge import PlatformAdminMfaPendingChallenge
from app.models.platform_admin_recovery_code import PlatformAdminRecoveryCode


async def get_platform_admin_by_email(db: AsyncSession, email: str) -> PlatformAdmin | None:
    result = await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))
    return result.scalar_one_or_none()


async def get_admin_mfa_pending_challenge(db: AsyncSession, token_hash: str) -> PlatformAdminMfaPendingChallenge | None:
    result = await db.execute(
        select(PlatformAdminMfaPendingChallenge).where(PlatformAdminMfaPendingChallenge.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def get_unused_admin_recovery_code(
    db: AsyncSession, platform_admin_id: UUID, code_hash: str
) -> PlatformAdminRecoveryCode | None:
    result = await db.execute(
        select(PlatformAdminRecoveryCode).where(
            PlatformAdminRecoveryCode.platform_admin_id == platform_admin_id,
            PlatformAdminRecoveryCode.code_hash == code_hash,
            PlatformAdminRecoveryCode.used_at.is_(None),
        )
    )
    return result.scalar_one_or_none()
```

- [ ] **Step 6: Update the router — imports, schema, and the admin-login/TOTP function bodies only**

In `backend/app/routers/platform_admin.py`:

1. Replace the top-of-file imports (down through the last inline `class` definition) with:

```python
import html
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import (
    AdminPrincipal,
    get_current_platform_admin,
    get_current_platform_admin_local,
)
from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthMethod, ConsentStatus, JobStatus, JobType, OrganizationStatus, UserRole
from app.models.job_run import JobRun
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.platform_admin import PlatformAdmin
from app.models.platform_admin_mfa_pending_challenge import PlatformAdminMfaPendingChallenge
from app.models.platform_admin_recovery_code import PlatformAdminRecoveryCode
from app.models.user import User
from app.repositories.platform_admin import (
    get_admin_mfa_pending_challenge,
    get_platform_admin_by_email,
    get_unused_admin_recovery_code,
)
from app.schemas.platform_admin import (
    AdminEnrollOtpConfirmRequest,
    AdminLoginRequest,
    AdminVerifyOtpRequest,
    ChangePasswordRequest,
    LocalUserCreateRequest,
    MailboxConnectionRequest,
    OrganizationCreateRequest,
    OrganizationUpdateRequest,
)
from app.services.auth import session_manager, totp
from app.services.auth.rate_limit import login_limiter, otp_limiter, rate_limiter
from app.services.auth.entra_links import entra_consent_urls
from app.services.auth.password import dummy_verify, hash_password, verify_password
from app.services.auth.session_manager import cookie_kwargs
from app.services.auth.tokens import hash_token, new_opaque_token

router = APIRouter(prefix="/admin", tags=["platform-admin"])

JOB_ERROR_WINDOW_DAYS = 7
_ADMIN_MFA_PENDING_COOKIE = settings.platform_admin_mfa_pending_cookie_name
```

(This step keeps `Domain`, `JobRun`, `DmarcAggregateReport`, `MailboxConnection`, `Organization`, `PlatformAdminMfaPendingChallenge`, `PlatformAdminRecoveryCode` model imports for now — Tasks 5-6 still use them inline. Only the schema classes moved out in this step; `_mailbox_out`, `_org_aggregates`, `_org_out`, and everything below `entra_consent_callback` in the original file are untouched by this task.)

2. In `admin_login`, replace:
```python
    result = await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == body.email))
    admin = result.scalar_one_or_none()
```
with:
```python
    admin = await get_platform_admin_by_email(db, body.email)
```

3. In `_get_pending_admin`, replace:
```python
    result = await db.execute(
        select(PlatformAdminMfaPendingChallenge).where(PlatformAdminMfaPendingChallenge.token_hash == token_hash)
    )
    challenge = result.scalar_one_or_none()
```
with:
```python
    challenge = await get_admin_mfa_pending_challenge(db, token_hash)
```

4. In `admin_verify_otp`, replace:
```python
        code_hash = totp.hash_recovery_code_for_lookup(code)
        result = await db.execute(
            select(PlatformAdminRecoveryCode).where(
                PlatformAdminRecoveryCode.platform_admin_id == admin.id,
                PlatformAdminRecoveryCode.code_hash == code_hash,
                PlatformAdminRecoveryCode.used_at.is_(None),
            )
        )
        recovery = result.scalar_one_or_none()
```
with:
```python
        code_hash = totp.hash_recovery_code_for_lookup(code)
        recovery = await get_unused_admin_recovery_code(db, admin.id, code_hash)
```

- [ ] **Step 7: Run tests, confirm all still pass**

Run: `cd backend && pytest tests/routers/test_platform_admin.py -v`
Expected: 6 passed.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/conftest.py backend/app/schemas/platform_admin.py backend/app/repositories/platform_admin.py backend/app/routers/platform_admin.py backend/tests/routers/test_platform_admin.py
git commit -m "Extract platform_admin.py schema; add repository layer for admin auth flow"
```

---

### Task 5: `platform_admin.py` organization CRUD + local user creation + mailbox connection admin override

**Files:**
- Modify: `backend/app/routers/platform_admin.py`
- Modify: `backend/tests/routers/test_platform_admin.py`

**Interfaces:**
- Consumes: `get_organization` (Plan A, `repositories/organizations.py`), `get_org_mailbox_connection` (Plan A, `repositories/mailbox_connections.py`), `login_as_platform_admin` (Plan A).

- [ ] **Step 1: Add organization CRUD / user-creation / mailbox-override tests**

Append to `backend/tests/routers/test_platform_admin.py`:

```python
import uuid

from app.models.organization import Organization

from tests.conftest import login_as_platform_admin


async def test_list_organizations_requires_admin(api):
    client, _owner_factory = api
    response = await client.get("/api/admin/organizations")
    assert response.status_code == 401


async def test_create_and_get_organization(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    create_response = await client.post("/api/admin/organizations", json={"name": "Acme Corp"})
    assert create_response.status_code == 201
    org_id = create_response.json()["id"]
    assert create_response.json()["domain_count"] == 0

    get_response = await client.get(f"/api/admin/organizations/{org_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Acme Corp"


async def test_get_organization_not_found(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/organizations/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


async def test_update_organization(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Old Name"})).json()["id"]

    response = await client.patch(f"/api/admin/organizations/{org_id}", json={"name": "New Name"})

    assert response.status_code == 200
    assert response.json()["name"] == "New Name"


async def test_delete_organization(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Deletable Org"})).json()["id"]

    response = await client.delete(f"/api/admin/organizations/{org_id}")

    assert response.status_code == 204
    assert (await client.get(f"/api/admin/organizations/{org_id}")).status_code == 404


async def test_delete_operator_organization_blocked(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Operator Org"})).json()["id"]

    async with owner_factory() as db:
        org = await db.get(Organization, uuid.UUID(org_id))
        org.is_operator = True
        await db.commit()

    response = await client.delete(f"/api/admin/organizations/{org_id}")

    assert response.status_code == 400


async def test_create_local_user_for_org(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Local Auth Org"})).json()["id"]

    response = await client.post(
        f"/api/admin/organizations/{org_id}/users", json={"email": "first-user@example.com"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "first-user@example.com"
    assert "setup_link" in body


async def test_create_local_user_rejects_entra_org(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    entra_tenant_id = str(uuid.uuid4())
    org_id = (
        await client.post("/api/admin/organizations", json={"name": "Entra Org", "entra_tenant_id": entra_tenant_id})
    ).json()["id"]

    response = await client.post(f"/api/admin/organizations/{org_id}/users", json={"email": "someone@example.com"})

    assert response.status_code == 409


async def test_upsert_mailbox_connection(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org_id = (await client.post("/api/admin/organizations", json={"name": "Mailbox Org"})).json()["id"]

    response = await client.post(
        f"/api/admin/organizations/{org_id}/mailbox-connection", json={"mailbox_address": "reports@example.com"}
    )

    assert response.status_code == 201
    assert response.json()["mailbox_address"] == "reports@example.com"

    update_response = await client.post(
        f"/api/admin/organizations/{org_id}/mailbox-connection",
        json={"mailbox_address": "reports@example.com", "consent_status": "granted"},
    )
    assert update_response.status_code == 201
    assert update_response.json()["consent_status"] == "granted"
```

- [ ] **Step 2: Run tests, confirm pass against the current router**

Run: `cd backend && pytest tests/routers/test_platform_admin.py -v`
Expected: 15 passed (6 from Task 4 + 9 new).

- [ ] **Step 3: Replace inline `Organization`/`MailboxConnection` lookups with the existing Plan-A repository functions**

Add these two imports to `backend/app/routers/platform_admin.py` (they weren't added in Task 4 because the router still had its own `get_organization` route function at that point — adding the repository import earlier would have been immediately shadowed by that same-named function defined lower in the file):

```python
from app.repositories.mailbox_connections import get_org_mailbox_connection
from app.repositories.organizations import get_organization
```

Then replace every occurrence of `await db.get(Organization, org_id)` (in the `get_organization` route, `update_organization`, `delete_organization`, `create_local_user`, `upsert_mailbox_connection`) with `await get_organization(db, org_id)`.

Note: this router already defines its own function named `get_organization` (the `GET /organizations/{org_id}` route handler) — this collides with the imported repository function of the same name. Rename the ROUTE function to `get_organization_route` (matching the exact same disambiguation pattern Plan A used for `list_sign_in_events`/`list_sign_in_events_route` in `sign_in_events.py` — FastAPI only cares about the `@router.get(...)` decorator, not the function's own name).

In `_mailbox_out`, replace:
```python
    result = await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org_id))
    connection = result.scalar_one_or_none()
```
with:
```python
    connection = await get_org_mailbox_connection(db, org_id)
```

**`upsert_mailbox_connection` has its own SEPARATE inline `select(MailboxConnection)` query — a second, distinct occurrence from `_mailbox_out`'s, easy to miss since both look identical.** Replace:
```python
    result = await db.execute(
        select(MailboxConnection).where(MailboxConnection.organization_id == org_id)
    )
    connection = result.scalar_one_or_none()
```
with:
```python
    connection = await get_org_mailbox_connection(db, org_id)
```
This is the exact same query, reused for its "does a connection already exist" upsert check — `get_org_mailbox_connection` returning `None` when there's no row yet is exactly what this function's `if connection is None:` branch already expects, so this is a pure substitution, not a behavior change.

- [ ] **Step 4: Run tests, confirm all still pass**

Run: `cd backend && pytest tests/routers/test_platform_admin.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/platform_admin.py backend/tests/routers/test_platform_admin.py
git commit -m "Migrate platform_admin.py org CRUD/user-creation/mailbox-override onto repository layer"
```

---

### Task 6: `platform_admin.py` org aggregates, job-runs, Entra consent callback

**Files:**
- Modify: `backend/app/routers/platform_admin.py`
- Modify: `backend/app/repositories/platform_admin.py`
- Modify: `backend/tests/routers/test_platform_admin.py`

**Interfaces:**
- Produces: `app.repositories.platform_admin.list_all_organizations(db) -> Sequence[Organization]`, `org_aggregates(db, org_ids: list[UUID]) -> dict[UUID, dict]`, `list_job_runs(db, *, limit, organization_id, job_type, status_filter, since_days) -> Sequence[JobRun]`, `job_runs_summary_stats(db) -> dict` (returns raw ORM objects/numbers; the router still shapes the JSON response, matching the codebase's existing repo-returns-data/router-shapes-output convention).

- [ ] **Step 1: Add org-list/aggregates/job-runs/consent-callback tests**

Append to `backend/tests/routers/test_platform_admin.py`:

```python
async def test_list_organizations_includes_aggregates(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    await client.post("/api/admin/organizations", json={"name": "Org One"})
    await client.post("/api/admin/organizations", json={"name": "Org Two"})

    response = await client.get("/api/admin/organizations")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    for org in body:
        assert org["domain_count"] == 0
        assert org["job_error_count_7d"] == 0
        assert org["last_report_at"] is None


async def test_job_runs_empty(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/job-runs")

    assert response.status_code == 200
    assert response.json() == []


async def test_job_runs_summary_empty(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/job-runs/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["last_failure"] is None
    assert body["success_rate_pct_24h"] is None
    assert body["reports_processed_today"] == 0


async def test_entra_consent_callback_success():
    """Unauthenticated, no DB fixture needed — this endpoint does no DB
    writes at all (see its own docstring)."""
    import httpx
    from httpx import ASGITransport

    from app.main import app

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/admin/entra/consent-callback")

    assert response.status_code == 200
    assert "Mail access granted" in response.text


async def test_entra_consent_callback_error():
    import httpx
    from httpx import ASGITransport

    from app.main import app

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/admin/entra/consent-callback", params={"error": "access_denied", "error_description": "User declined"}
        )

    assert response.status_code == 200
    assert "wasn't granted" in response.text
    assert "access_denied" in response.text
```

Note: `test_entra_consent_callback_success`/`_error` deliberately build their own bare `httpx.AsyncClient` rather than using the `api` fixture — this endpoint takes no `db` dependency at all and needs no authenticated user, so the full fixture (which sets up Postgres truncation, dependency overrides, etc.) is unnecessary machinery for it. This matches the endpoint's own docstring: "This intentionally does no DB writes."

- [ ] **Step 2: Run tests, confirm pass against the current router**

Run: `cd backend && pytest tests/routers/test_platform_admin.py -v`
Expected: 21 passed (15 from Task 5 + 6 new).

- [ ] **Step 3: Extract the org-aggregates/job-runs repository functions**

Append to `backend/app/repositories/platform_admin.py`:

```python
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func

from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import JobStatus, JobType
from app.models.job_run import JobRun
from app.models.organization import Organization

JOB_ERROR_WINDOW_DAYS = 7


async def list_all_organizations(db: AsyncSession) -> Sequence[Organization]:
    result = await db.execute(select(Organization).order_by(Organization.created_at.desc()))
    return result.scalars().all()


async def org_aggregates(db: AsyncSession, org_ids: list[UUID]) -> dict[UUID, dict]:
    """Batched per-org rollups for the admin organizations list — one GROUP
    BY query per metric across every org at once, not N queries per org.
    RLS is bypassed here the same way it is everywhere else in this router:
    get_current_platform_admin already set app.is_platform_admin=true on
    this transaction, which is what lets a query with no organization_id
    filter of its own see rows across every tenant."""
    if not org_ids:
        return {}

    domain_counts = dict(
        (
            await db.execute(
                select(Domain.organization_id, func.count())
                .where(Domain.organization_id.in_(org_ids))
                .group_by(Domain.organization_id)
            )
        ).all()
    )

    error_cutoff = datetime.now(timezone.utc) - timedelta(days=JOB_ERROR_WINDOW_DAYS)
    job_error_counts = dict(
        (
            await db.execute(
                select(JobRun.organization_id, func.count())
                .where(
                    JobRun.organization_id.in_(org_ids),
                    JobRun.status == JobStatus.failure,
                    JobRun.started_at >= error_cutoff,
                )
                .group_by(JobRun.organization_id)
            )
        ).all()
    )

    last_report_ats = dict(
        (
            await db.execute(
                select(DmarcAggregateReport.organization_id, func.max(DmarcAggregateReport.received_at))
                .where(DmarcAggregateReport.organization_id.in_(org_ids))
                .group_by(DmarcAggregateReport.organization_id)
            )
        ).all()
    )

    return {
        org_id: {
            "domain_count": domain_counts.get(org_id, 0),
            "job_error_count_7d": job_error_counts.get(org_id, 0),
            "last_report_at": (last_report_ats[org_id].isoformat() if last_report_ats.get(org_id) else None),
        }
        for org_id in org_ids
    }


async def list_job_runs(
    db: AsyncSession,
    *,
    limit: int,
    organization_id: UUID | None,
    job_type: JobType | None,
    status_filter: JobStatus | None,
    since_days: int | None,
) -> Sequence[JobRun]:
    query = select(JobRun).order_by(JobRun.started_at.desc())
    if organization_id is not None:
        query = query.where(JobRun.organization_id == organization_id)
    if job_type is not None:
        query = query.where(JobRun.job_type == job_type)
    if status_filter is not None:
        query = query.where(JobRun.status == status_filter)
    if since_days is not None:
        query = query.where(JobRun.started_at >= datetime.now(timezone.utc) - timedelta(days=since_days))
    query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def job_runs_summary_stats(db: AsyncSession) -> dict:
    """Bundles the /job-runs/summary dashboard-card stats in one call,
    matching this codebase's existing pattern of bundling multi-stat
    dashboard endpoints (see dmarc_reports.py's dmarc_posture) rather than
    one repository function per stat. Returns raw ORM objects/numbers —
    the router still shapes the JSON response."""
    last_failure = (
        await db.execute(
            select(JobRun).where(JobRun.status == JobStatus.failure).order_by(JobRun.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    total_24h, success_24h = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((JobRun.status == JobStatus.success, 1), else_=0)), 0),
            ).where(JobRun.started_at >= since_24h)
        )
    ).one()

    latest_mailbox_poll_at = (
        await db.execute(select(func.max(JobRun.started_at)).where(JobRun.job_type == JobType.mailbox_poll))
    ).scalar_one_or_none()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    reports_today = (
        await db.execute(
            select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.received_at >= today_start)
        )
    ).scalar_one()

    return {
        "last_failure": last_failure,
        "total_24h": total_24h,
        "success_24h": success_24h,
        "latest_mailbox_poll_at": latest_mailbox_poll_at,
        "reports_today": reports_today,
    }
```

- [ ] **Step 4: Update the router**

In `backend/app/routers/platform_admin.py`:

1. Add to the imports: `from app.repositories.platform_admin import (get_admin_mfa_pending_challenge, get_platform_admin_by_email, get_unused_admin_recovery_code, job_runs_summary_stats, list_all_organizations, list_job_runs, org_aggregates)` (merge with the existing `repositories.platform_admin` import line from Task 4 rather than duplicating it).

2. Remove the module-level `_org_aggregates` function entirely (now `org_aggregates` in the repository) and update `_org_out` to call `org_aggregates` (imported) instead of the local `_org_aggregates`.

3. In `list_organizations`, replace:
```python
    result = await db.execute(select(Organization).order_by(Organization.created_at.desc()))
    orgs = result.scalars().all()
    aggregates = await _org_aggregates(db, [org.id for org in orgs])
```
with:
```python
    orgs = await list_all_organizations(db)
    aggregates = await org_aggregates(db, [org.id for org in orgs])
```

4. In `list_job_runs` (the route — rename to `list_job_runs_route` to avoid shadowing the imported repository function `list_job_runs`, same disambiguation pattern as Step 3 of Task 5), replace the whole inline query-building block:
```python
    limit = max(1, min(limit, 200))
    query = select(JobRun).order_by(JobRun.started_at.desc())
    if organization_id is not None:
        query = query.where(JobRun.organization_id == organization_id)
    if job_type is not None:
        query = query.where(JobRun.job_type == job_type)
    if status_filter is not None:
        query = query.where(JobRun.status == status_filter)
    if since_days is not None:
        query = query.where(JobRun.started_at >= datetime.now(timezone.utc) - timedelta(days=since_days))
    query = query.limit(limit)

    result = await db.execute(query)
    return [
        ...
        for run in result.scalars().all()
    ]
```
with:
```python
    limit = max(1, min(limit, 200))
    runs = await list_job_runs(db, limit=limit, organization_id=organization_id, job_type=job_type, status_filter=status_filter, since_days=since_days)
    return [
        {
            "id": str(run.id),
            "job_type": run.job_type.value,
            "organization_id": str(run.organization_id) if run.organization_id else None,
            "domain_id": str(run.domain_id) if run.domain_id else None,
            "status": run.status.value,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error_message": run.error_message,
            "stats": run.stats,
        }
        for run in runs
    ]
```

5. In `job_runs_summary`, replace the whole body (everything between the docstring and the final `return`) with:
```python
    stats = await job_runs_summary_stats(db)
    last_failure = stats["last_failure"]
    success_rate_pct_24h = (
        round(stats["success_24h"] / stats["total_24h"] * 100, 1) if stats["total_24h"] else None
    )
    return {
        "last_failure": (
            {
                "job_type": last_failure.job_type.value,
                "organization_id": str(last_failure.organization_id) if last_failure.organization_id else None,
                "started_at": last_failure.started_at.isoformat(),
                "error_message": last_failure.error_message,
            }
            if last_failure is not None
            else None
        ),
        "success_rate_pct_24h": success_rate_pct_24h,
        "latest_mailbox_poll_at": stats["latest_mailbox_poll_at"].isoformat() if stats["latest_mailbox_poll_at"] else None,
        "reports_processed_today": int(stats["reports_today"]),
    }
```

6. Remove now-unused imports: `case`, `func` from `sqlalchemy` (verify nothing else in the file uses them — `_org_aggregates`/`_mailbox_out`'s old bodies are gone; check the rest of the file), `Domain`, `JobRun` model imports (verify — no longer queried directly in this file after these changes), `DmarcAggregateReport` (verify — check `_org_aggregates`'s old body was its only use). Keep `select` (still used for the mailbox-connection upsert's — wait, that already moved to `get_org_mailbox_connection` in Task 5 — grep to confirm whether `select` has ANY remaining use in this file after all these changes; if none, remove it too).

- [ ] **Step 5: Run tests, confirm all still pass**

Run: `cd backend && pytest tests/routers/test_platform_admin.py -v`
Expected: 21 passed.

- [ ] **Step 6: Run the FULL suite**

Run: `cd backend && pytest -v`
Expected: 208 (Task 3's cumulative total) + 21 (platform_admin.py) = 229 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/platform_admin.py backend/app/repositories/platform_admin.py backend/tests/routers/test_platform_admin.py
git commit -m "Migrate platform_admin.py org-aggregates/job-runs/consent-callback onto repository layer"
```

---

### Task 7: Final verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest -v`
Expected: 229 passed (192 from Plan A + 16 auth.py + 21 platform_admin.py — the 5 simple auth.py tests from Task 1 are counted in the 16).

- [ ] **Step 2: Full container build gate**

Run: `docker compose build api worker migrate`
Expected: all three images build cleanly.

- [ ] **Step 3: Confirm no stray duplication remains for the routers this plan touched**

Run: `grep -rn "class.*BaseModel" backend/app/routers/auth.py backend/app/routers/platform_admin.py`
Expected: no matches — every Pydantic model in both files moved to `schemas/`.

Run: `grep -c "await db.execute(select(" backend/app/routers/auth.py backend/app/routers/platform_admin.py`
Expected: 0 for both files (every inline `select()` moved to a repository function). If either is nonzero, check whether it's a genuine remaining case this plan's tasks didn't name — if so, that's a real gap to fix before this task is done, not a false positive to explain away.

- [ ] **Step 4: Confirm the rate-limiter reset didn't mask a real ordering bug**

Run the auth and platform-admin test files together, twice in a row, to rule out any test-order sensitivity the shared limiter reset might be hiding: `cd backend && pytest tests/routers/test_auth.py tests/routers/test_platform_admin.py tests/routers/test_auth.py tests/routers/test_platform_admin.py -v`
Expected: all pass both times through — if the second pass shows any 429s, the reset in the `api` fixture isn't actually running before every test (check fixture scope) and needs fixing before this task is done.

- [ ] **Step 5: Commit only if step 3 or 4 needed a fix**

If clean, this task needs no commit — it's a verification checkpoint.
