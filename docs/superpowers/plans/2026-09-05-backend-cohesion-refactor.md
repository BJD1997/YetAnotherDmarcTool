# Backend Cohesion Refactor (Plan A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a schema layer (`app/schemas/`) and a repository/query layer (`app/repositories/`) for 11 of the app's 14 routers, add HTTP-level integration tests that characterize and protect current behavior through the change, split `main.py`'s inline middlewares into their own modules, and dedupe the triplicated `_get_owned_domain()` helper.

**Architecture:** For each router: write integration tests against the router's *current* code first (they must pass immediately — this is characterization, not red/green TDD), then extract its Pydantic models into `app/schemas/<name>.py` and its `select()`/query logic into `app/repositories/<model>.py`, then re-run the same tests to prove behavior didn't change.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Postgres, pytest + pytest-asyncio (`asyncio_mode = auto`), httpx (`AsyncClient` + `ASGITransport`, already a pinned dependency — no new deps needed).

**Spec:** `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`

**Not in this plan (follow-up "Plan A2"):** `auth.py`, `platform_admin.py`, `dmarc_reports.py` — the 3 largest and most security-sensitive routers (OAuth, TOTP/MFA, rate limiting, the app's core reporting surface). They get their own plan so their tests get focused attention rather than being rushed through alongside simple CRUD routers. `repositories/dmarc_reports.py` is created here with only the handful of read functions the *other* routers need from it (see Task 2); Plan A2 will add to this same file, not create a second one.

## Global Constraints

- Work happens on a new branch `refactor/backend-cohesion` cut from `master`, in an isolated worktree (see `superpowers:using-git-worktrees`) — not on `v0.1.4-beta`, not directly on `master`.
- Every function in `app/repositories/*.py` takes `db: AsyncSession` as its first argument and returns data or raises `HTTPException` for a not-found/ownership check — no module-level caches, singletons, or other process-local state (required for horizontal scale-out; see spec).
- `pytest -v` (run from `backend/`, with `TEST_DATABASE_URL` set per `backend/tests/conftest.py`'s header) must be fully green before every commit.
- No behavior changes: an integration test that passes against the pre-refactor router must still pass, unmodified, after the schema/repository extraction for that router.
- Match existing code style: type hints everywhere, `datetime.now(timezone.utc)` (never naive `datetime.now()`), RLS's `refresh()`-before-`commit()` ordering for any RLS-protected model (see `app/db/rls.py`), no comments unless something is genuinely non-obvious.
- Local test DB (one-time setup, if not already running): `docker run -d --name dmarc-test-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=dmarc_test -p 55432:5432 postgres:16-alpine`, then export `TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/dmarc_test`.

---

### Task 1: HTTP test infrastructure + `organizations.py` (reference implementation)

**Files:**
- Modify: `backend/app/routers/organizations.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/app/schemas/organizations.py`
- Create: `backend/app/repositories/__init__.py` (empty)
- Create: `backend/app/repositories/organizations.py`
- Create: `backend/tests/routers/__init__.py` (empty)
- Create: `backend/tests/routers/test_organizations.py`

**Interfaces:**
- Produces (used by every later task): `api` pytest fixture yielding `(client: httpx.AsyncClient, owner_factory: async_sessionmaker)`; `seed_org_and_user(owner_factory, *, role=UserRole.org_admin, entra=False, is_demo_read_only=False) -> tuple[Organization, User]`; `login_as(client, owner_factory, user) -> None` — all in `tests/conftest.py`, imported via `from tests.conftest import login_as, seed_org_and_user`.
- Produces: `app.repositories.organizations.get_organization(db, organization_id) -> Organization | None`.
- Produces: `app.schemas.organizations.OrganizationUpdateRequest`.

- [ ] **Step 1: Add the HTTP test fixture and seeding helpers to `conftest.py`**

Add these imports to the top of `backend/tests/conftest.py` (alongside the existing ones):

```python
import uuid

import httpx
from httpx import ASGITransport

from app.config import settings
from app.db.session import get_db
from app.main import app
from app.models.enums import AuthMethod, OrganizationStatus, UserRole, UserStatus
from app.models.organization import Organization
from app.models.user import User
from app.services.auth import session_manager
```

Append to the end of the file:

```python
CSRF_HEADERS = {"X-Requested-With": "yetanotherdmarctool"}


@pytest_asyncio.fixture
async def api(migrated_db):
    """Yields (client, owner_factory) for HTTP-level router tests. `client` is
    an httpx.AsyncClient wired directly to the real ASGI app (no network
    socket) via ASGITransport, with the CSRF header pre-set (see
    enforce_csrf_header in app/main.py) so POST/PUT/PATCH/DELETE calls don't
    need to set it per-test. Requests run through app.db.session.get_db
    overridden to connect as the non-owner dmarc_app role — the same role
    FORCE ROW LEVEL SECURITY binds in prod — so RLS is genuinely exercised,
    not bypassed. `owner_factory` is for test setup that must bypass RLS
    (seeding orgs/users directly), same superuser role rls_sessions uses.
    """
    owner_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    app_engine = create_async_engine(_app_url(), poolclass=NullPool)
    owner_factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    app_factory = async_sessionmaker(app_engine, expire_on_commit=False)

    async with owner_factory() as owner:
        await owner.execute(text("TRUNCATE organizations CASCADE"))
        await owner.commit()

    async def _override_get_db():
        async with app_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=CSRF_HEADERS) as client:
        yield client, owner_factory

    app.dependency_overrides.pop(get_db, None)
    await app_engine.dispose()
    await owner_engine.dispose()


async def seed_org_and_user(
    owner_factory,
    *,
    role: UserRole = UserRole.org_admin,
    entra: bool = False,
    is_demo_read_only: bool = False,
) -> tuple[Organization, User]:
    """Inserts an Organization + User directly via the RLS-bypassing owner
    role — same idea as _seed_two_orgs in test_rls.py."""
    async with owner_factory() as db:
        org = Organization(
            name="Test Org",
            status=OrganizationStatus.active,
            entra_tenant_id=uuid.uuid4() if entra else None,
            is_demo_read_only=is_demo_read_only,
        )
        db.add(org)
        await db.flush()
        user = User(
            organization_id=org.id,
            email="admin@test.example",
            display_name="Test Admin",
            role=role,
            status=UserStatus.active,
            auth_method=AuthMethod.entra if entra else AuthMethod.local,
        )
        db.add(user)
        await db.flush()
        await db.refresh(org)
        await db.refresh(user)
        await db.commit()
        return org, user


async def login_as(client: httpx.AsyncClient, owner_factory, user: User) -> None:
    """Mints a real session (via the same session_manager the app uses) and
    sets it as a cookie on `client` — user_sessions is RLS-exempt, so the
    owner role is fine here."""
    async with owner_factory() as db:
        _, raw_token = await session_manager.create_user_session(
            db, user_id=user.id, organization_id=user.organization_id, ip_address="127.0.0.1", user_agent="pytest",
        )
        await db.commit()
    client.cookies.set(settings.session_cookie_name, raw_token)
```

- [ ] **Step 2: Write `test_organizations.py` against the current router**

```python
from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_get_current_organization(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/organizations/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(org.id)
    assert body["name"] == org.name
    assert body["status"] == "active"


async def test_get_current_organization_requires_auth(api):
    client, _owner_factory = api
    response = await client.get("/api/organizations/current")
    assert response.status_code == 401


async def test_update_current_organization_as_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.patch("/api/organizations/current", json={"name": "Renamed Org"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Org"


async def test_update_current_organization_requires_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    response = await client.patch("/api/organizations/current", json={"name": "Renamed Org"})

    assert response.status_code == 403
```

- [ ] **Step 3: Run the tests and confirm they pass against the current (unrefactored) router**

Run: `cd backend && pytest tests/routers/test_organizations.py -v`
Expected: 4 passed. If any fail, the fixture is wired wrong — fix it before proceeding (this step proves the new infra works before it's relied on for a refactor).

- [ ] **Step 4: Extract the schema**

Create `backend/app/schemas/organizations.py`:

```python
from pydantic import BaseModel

from app.models.enums import SpfAllQualifierMode


class OrganizationUpdateRequest(BaseModel):
    name: str
    spf_all_qualifier_mode: SpfAllQualifierMode | None = None
    hosted_mailbox_opt_in: bool | None = None
```

- [ ] **Step 5: Extract the repository**

Create `backend/app/repositories/__init__.py` (empty file).

Create `backend/app/repositories/organizations.py`:

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization


async def get_organization(db: AsyncSession, organization_id: UUID) -> Organization | None:
    return await db.get(Organization, organization_id)
```

- [ ] **Step 6: Update the router to use the new schema and repository**

Rewrite `backend/app/routers/organizations.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.organization import Organization
from app.models.user import User
from app.repositories.organizations import get_organization
from app.schemas.organizations import OrganizationUpdateRequest
from app.services.auth.entra_links import entra_consent_urls

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _org_out(org: Organization) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "status": org.status.value,
        "entra_tenant_id": str(org.entra_tenant_id) if org.entra_tenant_id else None,
        "is_operator": org.is_operator,
        "spf_all_qualifier_mode": org.spf_all_qualifier_mode.value,
        "hosted_mailbox_opt_in": org.hosted_mailbox_opt_in,
        "entra_consent_urls": entra_consent_urls(org),
    }


@router.get("/current")
async def get_current_organization(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    org = await get_organization(db, user.organization_id)
    return _org_out(org)


@router.patch("/current")
async def update_current_organization(
    body: OrganizationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    org = await get_organization(db, user.organization_id)
    org.name = body.name
    if body.spf_all_qualifier_mode is not None:
        org.spf_all_qualifier_mode = body.spf_all_qualifier_mode
    if body.hosted_mailbox_opt_in is not None:
        org.hosted_mailbox_opt_in = body.hosted_mailbox_opt_in
    await db.commit()
    await db.refresh(org)
    return _org_out(org)
```

- [ ] **Step 7: Run tests again to confirm behavior is unchanged**

Run: `cd backend && pytest tests/routers/test_organizations.py -v`
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/conftest.py backend/tests/routers/ backend/app/schemas/organizations.py backend/app/repositories/ backend/app/routers/organizations.py
git commit -m "Add HTTP test infra; extract organizations schema/repository layer"
```

---

### Task 2: `domains.py`

**Files:**
- Modify: `backend/app/routers/domains.py`
- Create: `backend/app/schemas/domains.py`
- Create: `backend/app/repositories/domains.py`
- Create: `backend/app/repositories/dmarc_reports.py`
- Create: `backend/tests/routers/test_domains.py`

**Interfaces:**
- Consumes: `api` fixture, `seed_org_and_user`, `login_as` (Task 1).
- Produces (used by Tasks 3-7): `app.repositories.domains.get_owned_domain(db, domain_id, organization_id) -> Domain` (raises `HTTPException(404)`), `list_domains_for_org(db, organization_id) -> Sequence[Domain]`, `count_domains_for_org(db, organization_id) -> int`, `count_verified_domains_for_org(db, organization_id) -> int`, `count_subdomains(db, domain_id) -> int`.
- Produces (used by Task 6 `onboarding.py`, and later Plan A2): `app.repositories.dmarc_reports.count_reports_for_org(db, organization_id) -> int`, `count_reports_for_domain(db, domain_id) -> int`, `last_report_received_at_for_domain(db, domain_id) -> datetime | None`, `failed_message_volume_for_domain(db, domain_id) -> int`.

- [ ] **Step 1: Write `test_domains.py` against the current router**

```python
import uuid

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus, UserRole

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org, **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_domains_empty(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/domains")

    assert response.status_code == 200
    assert response.json() == []


async def test_create_domain(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/domains", json={"name": "example.com"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "example.com"
    assert body["verification_status"] == "pending"


async def test_create_domain_rejects_invalid_name(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/domains", json={"name": "not a domain"})

    assert response.status_code == 422


async def test_get_domain_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_get_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "example.com"


async def test_update_domain_requires_admin(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/domains/{domain.id}", json={"notes": "hi"})

    assert response.status_code == 403


async def test_update_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/domains/{domain.id}", json={"notes": "hi", "is_active": False})

    assert response.status_code == 200
    body = response.json()
    assert body["notes"] == "hi"
    assert body["is_active"] is False


async def test_delete_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.delete(f"/api/domains/{domain.id}")

    assert response.status_code == 204


async def test_delete_domain_blocked_by_subdomain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    parent = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)
    await _add_domain(owner_factory, org, name="sub.example.com", parent_domain_id=parent.id)
    await login_as(client, owner_factory, user)

    response = await client.delete(f"/api/domains/{parent.id}")

    assert response.status_code == 409


async def test_ranked_domains_includes_unverified_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/domains/ranked")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["not_verified"] is True
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_domains.py -v`
Expected: 10 passed.

- [ ] **Step 3: Extract the schema**

Create `backend/app/schemas/domains.py`:

```python
import re
import uuid

from pydantic import BaseModel, field_validator

from app.models.enums import DomainMailProfile

_DOMAIN_NAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class DomainCreateRequest(BaseModel):
    name: str
    parent_domain_id: uuid.UUID | None = None
    notes: str | None = None
    mail_profile: DomainMailProfile | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not _DOMAIN_NAME_RE.match(value):
            raise ValueError("not a valid domain name")
        return value


class DomainUpdateRequest(BaseModel):
    notes: str | None = None
    is_active: bool | None = None
    mail_profile: DomainMailProfile | None = None
```

- [ ] **Step 4: Extract the domains repository**

Create `backend/app/repositories/domains.py`:

```python
from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus


async def get_owned_domain(db: AsyncSession, domain_id: UUID, organization_id: UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return domain


async def list_domains_for_org(db: AsyncSession, organization_id: UUID) -> Sequence[Domain]:
    result = await db.execute(
        select(Domain).where(Domain.organization_id == organization_id).order_by(Domain.name)
    )
    return result.scalars().all()


async def count_domains_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Domain).where(Domain.organization_id == organization_id)
    )
    return result.scalar_one()


async def count_verified_domains_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Domain).where(
            Domain.organization_id == organization_id,
            Domain.verification_status == DomainVerificationStatus.verified,
        )
    )
    return result.scalar_one()


async def count_subdomains(db: AsyncSession, domain_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Domain).where(Domain.parent_domain_id == domain_id)
    )
    return result.scalar_one()
```

- [ ] **Step 5: Create the (minimal) dmarc_reports repository**

Create `backend/app/repositories/dmarc_reports.py` — Plan A2 will add more functions here when the `dmarc_reports.py` router itself is migrated; these four are only what `domains.py` and (Task 6) `onboarding.py` need today:

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.enums import AuthResult


async def count_reports_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(
            DmarcAggregateReport.organization_id == organization_id
        )
    )
    return result.scalar_one()


async def count_reports_for_domain(db: AsyncSession, domain_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.domain_id == domain_id)
    )
    return result.scalar_one()


async def last_report_received_at_for_domain(db: AsyncSession, domain_id: UUID) -> datetime | None:
    result = await db.execute(
        select(func.max(DmarcAggregateReport.received_at)).where(DmarcAggregateReport.domain_id == domain_id)
    )
    return result.scalar_one_or_none()


async def failed_message_volume_for_domain(db: AsyncSession, domain_id: UUID) -> int:
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    result = await db.execute(
        select(func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)).where(
            DmarcAggregateRecord.domain_id == domain_id
        )
    )
    return result.scalar_one()
```

- [ ] **Step 6: Update the router**

Rewrite `backend/app/routers/domains.py` (the docstrings/comments on individual endpoints are unchanged from the current file — omitted below for brevity where a function body is otherwise identical, but copy them across, don't drop them):

```python
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.domain import Domain
from app.models.enums import DomainMailProfile, DomainVerificationStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.user import User
from app.repositories.dmarc_reports import (
    count_reports_for_domain,
    failed_message_volume_for_domain,
    last_report_received_at_for_domain,
)
from app.repositories.domains import count_subdomains, get_owned_domain, list_domains_for_org
from app.schemas.domains import DomainCreateRequest, DomainUpdateRequest
from app.services.cloudflare.dns_provisioner import ensure_authorization_record
from app.services.dns_checks.dmarc_record import check_rua_destination
from app.services.dns_checks.domain_verification import apply_domain_verification, verification_record_name
from app.services.ingestion.report_writer import resweep_domain_records, resweep_unmatched_reports
from app.services.rating.domain_rating import compute_domain_rating, domain_policy_readiness, latest_findings_by_type
from app.services.rating.score import tally_worst_status

router = APIRouter(prefix="/domains", tags=["domains"])


def _domain_out(domain: Domain) -> dict:
    return {
        "id": str(domain.id),
        "name": domain.name,
        "parent_domain_id": str(domain.parent_domain_id) if domain.parent_domain_id else None,
        "notes": domain.notes,
        "is_active": domain.is_active,
        "created_at": domain.created_at.isoformat(),
        "verification_status": domain.verification_status.value,
        "verified_at": domain.verified_at.isoformat() if domain.verified_at else None,
        "verification_token": domain.verification_token,
        "verification_record_name": verification_record_name(domain.name),
        "mail_profile": domain.mail_profile.value,
        "hosted_report_address": domain.hosted_report_address,
    }


@router.get("")
async def list_domains(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    domains = await list_domains_for_org(db, user.organization_id)
    return [_domain_out(d) for d in domains]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_domain(
    body: DomainCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    parent: Domain | None = None
    if body.parent_domain_id is not None:
        parent = await db.get(Domain, body.parent_domain_id)
        if parent is None or parent.organization_id != user.organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_domain_id not found in your organization")
        if parent.parent_domain_id is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "parent_domain_id must itself be an apex domain, not a subdomain"
            )
        if not body.name.endswith(f".{parent.name}"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{body.name!r} is not a subdomain of {parent.name!r}"
            )

    domain = Domain(
        organization_id=user.organization_id,
        parent_domain_id=body.parent_domain_id,
        name=body.name,
        notes=body.notes,
        mail_profile=body.mail_profile or DomainMailProfile.sends_mail,
    )
    if parent is not None and parent.verification_status == DomainVerificationStatus.verified:
        domain.verification_status = DomainVerificationStatus.verified
        domain.verified_at = datetime.now(timezone.utc)

    db.add(domain)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "this domain is already registered")

    resweep_counts = await resweep_unmatched_reports(db, user.organization_id)
    reattributed_records = await resweep_domain_records(db, user.organization_id, domain)

    await db.refresh(domain)
    await db.commit()
    return {
        **_domain_out(domain),
        "reattributed_reports": resweep_counts["aggregate_reports"],
        "reattributed_records": reattributed_records,
    }


@router.get("/ranked")
async def ranked_domains(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    domains = await list_domains_for_org(db, user.organization_id)

    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == user.organization_id))
    ).scalar_one_or_none()
    mailbox_address = connection.mailbox_address if connection is not None else None

    items = []
    for domain in domains:
        not_verified = domain.verification_status != DomainVerificationStatus.verified
        score: float | None = None
        grade: str | None = None
        insufficient_data = True
        message_volume = 0
        check_status_counts = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
        ready_to_enforce = False
        current_policy: str | None = None
        dmarc_configured: bool | None = None
        rua_status: str | None = None
        last_dns_check_at = None

        if not not_verified:
            findings_by_type = await latest_findings_by_type(db, domain.id)
            check_status_counts = tally_worst_status(findings_by_type)
            rating, message_volume = await compute_domain_rating(db, domain, findings_by_type=findings_by_type)
            score = rating.score
            grade = rating.grade
            insufficient_data = rating.insufficient_data

            readiness = await domain_policy_readiness(db, domain, rating=rating, total_volume=message_volume)
            current_policy = readiness.latest_policy
            ready_to_enforce = readiness.ready

            if insufficient_data:
                dmarc_findings = findings_by_type.get(CheckType.dmarc, [])
                if dmarc_findings:
                    dmarc_configured = not (
                        len(dmarc_findings) == 1 and dmarc_findings[0].summary == "No DMARC record found"
                    )
                    last_dns_check_at = dmarc_findings[0].checked_at
                elif findings_by_type:
                    last_dns_check_at = next(iter(findings_by_type.values()))[0].checked_at

                if mailbox_address is not None:
                    rua_result = await check_rua_destination(domain.name, mailbox_address)
                    rua_status = rua_result.status

        last_report_at = await last_report_received_at_for_domain(db, domain.id)
        failed_volume = await failed_message_volume_for_domain(db, domain.id)

        items.append(
            {
                "domain_id": str(domain.id),
                "name": domain.name,
                "not_verified": not_verified,
                "insufficient_data": insufficient_data,
                "score": score,
                "grade": grade,
                "message_volume": message_volume,
                "failed_volume": int(failed_volume),
                "current_policy": current_policy,
                "last_report_at": last_report_at.isoformat() if last_report_at else None,
                "check_status_counts": check_status_counts,
                "ready_to_enforce": ready_to_enforce,
                "dmarc_configured": dmarc_configured,
                "rua_status": rua_status,
                "last_dns_check_at": last_dns_check_at.isoformat() if last_dns_check_at else None,
                "mail_profile": domain.mail_profile.value,
            }
        )

    def sort_key(item: dict) -> tuple:
        if item["not_verified"]:
            return (0, 0.0)
        if item["insufficient_data"]:
            return (2, 0.0)
        return (1, item["score"])

    items.sort(key=sort_key)
    return items


@router.get("/{domain_id}")
async def get_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    domain = await get_owned_domain(db, domain_id, user.organization_id)
    return _domain_out(domain)


@router.post("/{domain_id}/verify")
async def verify_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    domain = await get_owned_domain(db, domain_id, user.organization_id)

    ok = await apply_domain_verification(db, domain)
    if not ok:
        return {"verified": False, "domain": _domain_out(domain)}

    await db.flush()
    await db.refresh(domain)
    await db.commit()
    return {"verified": True, "domain": _domain_out(domain)}


@router.patch("/{domain_id}")
async def update_domain(
    domain_id: uuid.UUID,
    body: DomainUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    domain = await get_owned_domain(db, domain_id, user.organization_id)
    if body.notes is not None:
        domain.notes = body.notes
    if body.is_active is not None:
        domain.is_active = body.is_active
    if body.mail_profile is not None:
        domain.mail_profile = body.mail_profile
    await db.flush()
    await db.refresh(domain)
    await db.commit()
    return _domain_out(domain)


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> None:
    domain = await get_owned_domain(db, domain_id, user.organization_id)

    if await count_subdomains(db, domain_id) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "remove or reassign subdomains first")

    if await count_reports_for_domain(db, domain_id) > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "domain has report history — archive it instead (PATCH is_active=false)"
        )

    await db.delete(domain)
    await db.commit()


def _hosted_mailbox_available(org: Organization) -> bool:
    return org.entra_tenant_id is None or org.hosted_mailbox_opt_in


@router.post("/{domain_id}/hosted-report-address")
async def get_or_create_hosted_report_address(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    domain = await get_owned_domain(db, domain_id, user.organization_id)

    org = await db.get(Organization, user.organization_id)
    if not _hosted_mailbox_available(org):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "hosted mailbox isn't enabled for your organization — see Settings")

    if not settings.hosted_reports_address_domain or not settings.hosted_reports_mailbox_address:
        raise HTTPException(status.HTTP_409_CONFLICT, "hosted reporting addresses aren't configured on this instance yet")

    if domain.hosted_report_address is None:
        mailbox_local_part = settings.hosted_reports_mailbox_address.split("@", 1)[0]
        domain.hosted_report_address = f"{mailbox_local_part}+{secrets.token_hex(6)}@{settings.hosted_reports_address_domain}"
        await db.flush()
        await db.refresh(domain)
        await db.commit()

    provision_result = await ensure_authorization_record(domain.name)

    return {
        "hosted_report_address": domain.hosted_report_address,
        "authorization_record_status": provision_result.status,
        "authorization_record_detail": provision_result.detail,
    }
```

The complete, correct import block for the rewritten file (replace the whole top-of-file import section with exactly this — `re`, `secrets`'s sibling `BaseModel`/`field_validator`, `case`/`func`, `AuthResult`, and `DmarcAggregateRecord`/`DmarcAggregateReport` are all gone because their only uses moved into `schemas/domains.py` or `repositories/dmarc_reports.py`; `select` stays because `ranked_domains` still runs one raw `MailboxConnection` lookup that wasn't extracted):

```python
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.domain import Domain
from app.models.enums import CheckType, DomainMailProfile, DomainVerificationStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.user import User
from app.repositories.dmarc_reports import (
    count_reports_for_domain,
    failed_message_volume_for_domain,
    last_report_received_at_for_domain,
)
from app.repositories.domains import count_subdomains, get_owned_domain, list_domains_for_org
from app.schemas.domains import DomainCreateRequest, DomainUpdateRequest
from app.services.cloudflare.dns_provisioner import ensure_authorization_record
from app.services.dns_checks.dmarc_record import check_rua_destination
from app.services.dns_checks.domain_verification import apply_domain_verification, verification_record_name
from app.services.ingestion.report_writer import resweep_domain_records, resweep_unmatched_reports
from app.services.rating.domain_rating import compute_domain_rating, domain_policy_readiness, latest_findings_by_type
from app.services.rating.score import tally_worst_status
```

- [ ] **Step 7: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_domains.py -v`
Expected: 10 passed.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/domains.py backend/app/schemas/domains.py backend/app/repositories/domains.py backend/app/repositories/dmarc_reports.py backend/tests/routers/test_domains.py
git commit -m "Extract domains schema/repository layer; add dmarc_reports repository stub"
```

---

### Task 3: `mailbox_connections.py`

**Files:**
- Modify: `backend/app/routers/mailbox_connections.py`
- Create: `backend/app/schemas/mailbox_connections.py`
- Create: `backend/app/repositories/mailbox_connections.py`
- Create: `backend/tests/routers/test_mailbox_connections.py`

**Interfaces:**
- Consumes: `api`, `seed_org_and_user`, `login_as` (Task 1).
- Produces (used by Task 6 `onboarding.py`, Task 7 `action_queue.py`): `app.repositories.mailbox_connections.get_org_mailbox_connection(db, organization_id) -> MailboxConnection | None`.

- [ ] **Step 1: Write `test_mailbox_connections.py` against the current router**

```python
from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_get_mailbox_connection_not_found(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/mailbox-connection")

    assert response.status_code == 404


async def test_set_mailbox_connection_requires_entra_tenant(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    assert response.status_code == 409


async def test_set_mailbox_connection(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["mailbox_address"] == "reports@example.com"
    assert body["consent_status"] == "granted"


async def test_get_mailbox_connection_after_set(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)
    await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    response = await client.get("/api/mailbox-connection")

    assert response.status_code == 200
    assert response.json()["mailbox_address"] == "reports@example.com"


async def test_mailbox_job_runs_empty(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/mailbox-connection/job-runs")

    assert response.status_code == 200
    assert response.json() == []
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_mailbox_connections.py -v`
Expected: 5 passed.

- [ ] **Step 3: Extract the schema**

Create `backend/app/schemas/mailbox_connections.py`:

```python
from pydantic import BaseModel


class MailboxConnectionSetRequest(BaseModel):
    mailbox_address: str
```

- [ ] **Step 4: Extract the repository**

Create `backend/app/repositories/mailbox_connections.py`:

```python
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import JobType
from app.models.job_run import JobRun
from app.models.mailbox_connection import MailboxConnection


async def get_org_mailbox_connection(db: AsyncSession, organization_id: UUID) -> MailboxConnection | None:
    result = await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == organization_id))
    return result.scalar_one_or_none()


async def list_mailbox_job_runs(db: AsyncSession, organization_id: UUID, *, limit: int) -> Sequence[JobRun]:
    result = await db.execute(
        select(JobRun)
        .where(JobRun.organization_id == organization_id, JobRun.job_type == JobType.mailbox_poll)
        .order_by(JobRun.started_at.desc())
        .limit(limit)
    )
    return result.scalars().all()
```

- [ ] **Step 5: Update the router**

Rewrite `backend/app/routers/mailbox_connections.py` (replace the `_get_org_connection`/inline job-run query, keep everything else — including the `_connection_out`/`_mailbox_health_extra` helpers and all docstrings — unchanged):

```python
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.user import User
from app.repositories.mailbox_connections import get_org_mailbox_connection, list_mailbox_job_runs
from app.schemas.mailbox_connections import MailboxConnectionSetRequest
from app.workers.jobs.mailbox_poll_job import poll_org_mailbox

router = APIRouter(prefix="/mailbox-connection", tags=["mailbox-connection"])


def _connection_out(connection: MailboxConnection) -> dict:
    return {
        "id": str(connection.id),
        "mailbox_address": connection.mailbox_address,
        "consent_status": connection.consent_status.value,
        "consent_granted_at": connection.consent_granted_at.isoformat() if connection.consent_granted_at else None,
        "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        "last_sync_status": connection.last_sync_status.value if connection.last_sync_status else None,
        "last_sync_error": connection.last_sync_error,
    }


async def _mailbox_health_extra(db: AsyncSession, organization_id) -> dict:
    last_report_at = (
        await db.execute(
            select(func.max(DmarcAggregateReport.received_at)).where(
                DmarcAggregateReport.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()

    job_runs = await list_mailbox_job_runs(db, organization_id, limit=1)
    last_run = job_runs[0] if job_runs else None

    return {
        "last_report_at": last_report_at.isoformat() if last_report_at else None,
        "last_run_stats": last_run.stats if last_run is not None else None,
    }


@router.get("")
async def get_mailbox_connection(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mailbox connection configured for your organization yet")
    extra = await _mailbox_health_extra(db, user.organization_id)
    return {**_connection_out(connection), **extra}


@router.get("/job-runs")
async def mailbox_job_runs(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    runs = await list_mailbox_job_runs(db, user.organization_id, limit=limit)
    return [
        {
            "id": str(r.id),
            "status": r.status.value,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error_message": r.error_message,
            "stats": r.stats,
        }
        for r in runs
    ]


@router.put("", status_code=status.HTTP_200_OK)
async def set_mailbox_connection(
    body: MailboxConnectionSetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    org = await db.get(Organization, user.organization_id)
    if org.entra_tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set yet — contact your platform administrator")

    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        connection = MailboxConnection(organization_id=user.organization_id, mailbox_address=body.mailbox_address)
        db.add(connection)
    elif connection.mailbox_address != body.mailbox_address:
        connection.mailbox_address = body.mailbox_address
        connection.delta_link = None

    connection.consent_status = ConsentStatus.granted
    connection.consent_granted_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(connection)
    await db.commit()

    background_tasks.add_task(poll_org_mailbox, organization_id=org.id, tenant_id=str(org.entra_tenant_id))

    return _connection_out(connection)


@router.post("/resync", status_code=status.HTTP_202_ACCEPTED)
async def resync_mailbox_connection(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mailbox connection configured for your organization yet")

    org = await db.get(Organization, user.organization_id)
    if org.entra_tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set")

    background_tasks.add_task(poll_org_mailbox, organization_id=org.id, tenant_id=str(org.entra_tenant_id))
    return {"status": "resync started"}
```

- [ ] **Step 6: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_mailbox_connections.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/mailbox_connections.py backend/app/schemas/mailbox_connections.py backend/app/repositories/mailbox_connections.py backend/tests/routers/test_mailbox_connections.py
git commit -m "Extract mailbox_connections schema/repository layer"
```

---

### Task 4: `dns_checks.py`

**Files:**
- Modify: `backend/app/routers/dns_checks.py`
- Create: `backend/app/repositories/dns_checks.py`
- Create: `backend/tests/routers/test_dns_checks.py`

**Interfaces:**
- Consumes: `api`, `seed_org_and_user`, `login_as` (Task 1); `get_owned_domain` (Task 2, `app.repositories.domains`).
- Produces (used by Task 6 `onboarding.py`): `app.repositories.dns_checks.count_dns_checks_for_org(db, organization_id) -> int`.

This router has no Pydantic request models (all params are path/query) — no schema extraction needed, only repository extraction and dedup of the local `_get_owned_domain`.

- [ ] **Step 1: Write `test_dns_checks.py` against the current router**

```python
from datetime import datetime, timezone

from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import CheckStatus, CheckType, UserRole

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org, **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_latest_checks_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_latest_checks_returns_latest_run_only(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.fail,
                summary="stale finding", checked_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.pass_,
                summary="current finding", checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["summary"] == "current finding"


async def test_list_latest_checks_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    other_org, _other_user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, other_org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 404


async def test_tls_rpt_summary_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_reports"] == 0
    assert body["failure_rate_pct"] is None


async def test_recheck_domain_requires_verified(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/checks/recheck")

    assert response.status_code == 409
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_dns_checks.py -v`
Expected: 5 passed.

- [ ] **Step 3: Extract the repository**

Create `backend/app/repositories/dns_checks.py`:

```python
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dns_check import DnsCheckResult


async def count_dns_checks_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(DnsCheckResult).where(DnsCheckResult.organization_id == organization_id)
    )
    return result.scalar_one()


async def list_latest_check_results(db: AsyncSession, domain_id: UUID) -> Sequence[DnsCheckResult]:
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at))
        .where(DnsCheckResult.domain_id == domain_id)
        .scalar_subquery()
    )
    result = await db.execute(
        select(DnsCheckResult)
        .where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.checked_at == latest_ts)
        .order_by(DnsCheckResult.check_type, DnsCheckResult.subject.nulls_first())
    )
    return result.scalars().all()
```

- [ ] **Step 4: Update the router — replace the local `_get_owned_domain` and inline queries**

Replace the import block at the top of `backend/app/routers/dns_checks.py` with exactly this (`Domain` is dropped — after this edit nothing in the file references the class directly, only `domain` instances returned from `get_owned_domain`; `func` is dropped — its only use was the now-extracted `list_latest_checks` query; `select` stays — `dmarc_rua_check` and `tls_rpt_builder` still each run one raw `MailboxConnection` lookup):

```python
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dns_check import DnsCheckResult
from app.models.enums import DomainVerificationStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.tls_rpt import TlsRptReport
from app.models.user import User
from app.repositories.dns_checks import list_latest_check_results
from app.repositories.domains import get_owned_domain
from app.services.dns_checks.base import is_null_mx
from app.services.dns_checks.dmarc_record import check_rua_destination
from app.services.dns_checks.inbound_view import build_inbound_hosts
from app.services.dns_checks.mta_sts import _TXT_RE, _mx_covered, _parse_policy, fetch_policy_file
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx, resolve_txt_strict
from app.services.dns_checks.scheduled_recheck import run_and_persist_checks
from app.services.dns_checks.tls_rpt_check import check_tls_rpt_rua_destination, fetch_current_tls_rpt_record
```

Then:

1. Remove the local `_get_owned_domain` function (the 5-line function directly below the imports) entirely.
2. Replace every call site of `_get_owned_domain(db, domain_id, user.organization_id)` with `get_owned_domain(db, domain_id, user.organization_id)` — this is a pure rename, 6 call sites, one each in `list_latest_checks`, `inbound_hosts`, `dmarc_rua_check`, `recheck_domain`, `mta_sts_builder`, `tls_rpt_builder`.
3. Replace the body of `list_latest_checks` with:

```python
@router.get("/domains/{domain_id}/checks")
async def list_latest_checks(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    await get_owned_domain(db, domain_id, user.organization_id)
    rows = await list_latest_check_results(db, domain_id)
    return [_result_out(r) for r in rows]
```

Everything else in the file (the TLS-RPT helper/endpoints, `mta_sts_builder`, `tls_rpt_builder`) is unchanged — none of it is duplicated elsewhere, so it stays as router-local logic per the spec's non-goal of not restructuring beyond the identified drift.

- [ ] **Step 5: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_dns_checks.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/dns_checks.py backend/app/repositories/dns_checks.py backend/tests/routers/test_dns_checks.py
git commit -m "Extract dns_checks repository; dedupe get_owned_domain onto repositories.domains"
```

---

### Task 5: `selectors.py`

**Files:**
- Modify: `backend/app/routers/selectors.py`
- Create: `backend/app/schemas/selectors.py`
- Create: `backend/app/repositories/selectors.py`
- Modify: `backend/app/repositories/dmarc_reports.py` (add one function)
- Create: `backend/tests/routers/test_selectors.py`

**Interfaces:**
- Consumes: `api`, `seed_org_and_user`, `login_as` (Task 1); `get_owned_domain` (Task 2).

- [ ] **Step 1: Write `test_selectors.py` against the current router**

```python
from app.models.domain import Domain
from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_selectors_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/selectors")

    assert response.status_code == 200
    assert response.json() == []


async def test_create_selector(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    assert response.status_code == 201
    assert response.json()["selector"] == "google"


async def test_create_selector_rejects_invalid_format(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "not valid!"})

    assert response.status_code == 422


async def test_create_duplicate_selector_conflicts(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)
    await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    assert response.status_code == 409


async def test_delete_selector(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)
    created = (await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})).json()

    response = await client.delete(f"/api/domains/{domain.id}/selectors/{created['id']}")

    assert response.status_code == 204


async def test_detected_selectors_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/selectors/detected")

    assert response.status_code == 200
    assert response.json() == []
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_selectors.py -v`
Expected: 6 passed.

- [ ] **Step 3: Extract the schema**

Create `backend/app/schemas/selectors.py`:

```python
import re

from pydantic import BaseModel, field_validator

_SELECTOR_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?)*$")


class SelectorCreateRequest(BaseModel):
    selector: str
    description: str | None = None

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        value = value.strip()
        if not _SELECTOR_RE.match(value):
            raise ValueError("not a valid DKIM selector")
        return value
```

- [ ] **Step 4: Extract the selectors repository, and add one function to the dmarc_reports repository**

Create `backend/app/repositories/selectors.py`:

```python
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dkim_selector import DkimSelector


async def list_selectors_for_domain(db: AsyncSession, domain_id: UUID) -> Sequence[DkimSelector]:
    result = await db.execute(
        select(DkimSelector).where(DkimSelector.domain_id == domain_id).order_by(DkimSelector.selector)
    )
    return result.scalars().all()


async def known_selector_names(db: AsyncSession, domain_id: UUID) -> set[str]:
    result = await db.execute(select(DkimSelector.selector).where(DkimSelector.domain_id == domain_id))
    return set(result.scalars().all())
```

Add to `backend/app/repositories/dmarc_reports.py` (append; imports for `DmarcAggregateRecord` already present in that file from Task 2):

```python
async def list_auth_results_for_domain(db: AsyncSession, domain_id: UUID) -> Sequence[tuple]:
    result = await db.execute(
        select(DmarcAggregateRecord.auth_results, DmarcAggregateRecord.report_id, DmarcAggregateRecord.count).where(
            DmarcAggregateRecord.domain_id == domain_id
        )
    )
    return result.all()
```

This needs `from collections.abc import Sequence` added to that file's imports (alongside the existing `datetime`/`uuid` ones).

- [ ] **Step 5: Update the router**

Rewrite `backend/app/routers/selectors.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dkim_selector import DkimSelector
from app.models.user import User
from app.repositories.dmarc_reports import list_auth_results_for_domain
from app.repositories.domains import get_owned_domain
from app.repositories.selectors import known_selector_names, list_selectors_for_domain
from app.schemas.selectors import SelectorCreateRequest
from app.services.dmarc_narrative import describe_alignment

router = APIRouter(prefix="/domains/{domain_id}/selectors", tags=["dkim-selectors"])


def _selector_out(sel: DkimSelector) -> dict:
    return {
        "id": str(sel.id),
        "domain_id": str(sel.domain_id),
        "selector": sel.selector,
        "description": sel.description,
        "created_at": sel.created_at.isoformat(),
    }


@router.get("")
async def list_selectors(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    await get_owned_domain(db, domain_id, user.organization_id)
    selectors = await list_selectors_for_domain(db, domain_id)
    return [_selector_out(s) for s in selectors]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_selector(
    domain_id: uuid.UUID,
    body: SelectorCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    await get_owned_domain(db, domain_id, user.organization_id)

    selector = DkimSelector(
        organization_id=user.organization_id,
        domain_id=domain_id,
        selector=body.selector,
        description=body.description,
    )
    db.add(selector)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "this selector is already registered for this domain")
    await db.refresh(selector)
    await db.commit()
    return _selector_out(selector)


@router.get("/detected")
async def detected_selectors(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    domain = await get_owned_domain(db, domain_id, user.organization_id)
    known = await known_selector_names(db, domain_id)
    rows = await list_auth_results_for_domain(db, domain_id)

    stats: dict[str, dict] = {}
    for auth_results, report_id, count in rows:
        for entry in (auth_results or {}).get("dkim") or []:
            selector = entry.get("selector")
            dkim_domain = entry.get("domain")
            if not selector or selector in known:
                continue
            if describe_alignment(dkim_domain, domain.name) == "none":
                continue
            s = stats.setdefault(selector, {"report_ids": set(), "message_volume": 0})
            s["report_ids"].add(report_id)
            s["message_volume"] += count

    items = [
        {"selector": selector, "report_count": len(s["report_ids"]), "message_volume": s["message_volume"]}
        for selector, s in stats.items()
    ]
    items.sort(key=lambda x: -x["message_volume"])
    return items


@router.delete("/{selector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_selector(
    domain_id: uuid.UUID,
    selector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> None:
    await get_owned_domain(db, domain_id, user.organization_id)
    selector = await db.get(DkimSelector, selector_id)
    if selector is None or selector.domain_id != domain_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "selector not found")
    await db.delete(selector)
    await db.commit()
```

- [ ] **Step 6: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_selectors.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/selectors.py backend/app/schemas/selectors.py backend/app/repositories/selectors.py backend/app/repositories/dmarc_reports.py backend/tests/routers/test_selectors.py
git commit -m "Extract selectors schema/repository layer; dedupe get_owned_domain"
```

---

### Task 6: `sign_in_events.py`

**Files:**
- Modify: `backend/app/routers/sign_in_events.py`
- Create: `backend/app/repositories/sign_in_events.py`
- Create: `backend/tests/routers/test_sign_in_events.py`

No Pydantic models in this router — repository extraction only.

- [ ] **Step 1: Write `test_sign_in_events.py` against the current router**

```python
from datetime import datetime, timezone

from app.models.enums import AuthMethod, SignInResult, UserRole
from app.models.sign_in_event import SignInEvent

from tests.conftest import login_as, seed_org_and_user


async def test_list_sign_in_events_requires_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/sign-in-events")

    assert response.status_code == 403


async def test_list_sign_in_events(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        db.add(
            SignInEvent(
                organization_id=org.id, attempted_email="a@example.com", auth_method=AuthMethod.local,
                result=SignInResult.success, created_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/sign-in-events")

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["email"] == "a@example.com"
    assert body["has_more"] is False


async def test_list_sign_in_events_filters_by_result(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        db.add(
            SignInEvent(
                organization_id=org.id, attempted_email="ok@example.com", auth_method=AuthMethod.local,
                result=SignInResult.success, created_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            SignInEvent(
                organization_id=org.id, attempted_email="bad@example.com", auth_method=AuthMethod.local,
                result=SignInResult.failure, created_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/sign-in-events", params={"result": "failure"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["email"] == "bad@example.com"
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_sign_in_events.py -v`
Expected: 3 passed.

- [ ] **Step 3: Extract the repository**

Create `backend/app/repositories/sign_in_events.py`:

```python
import uuid
from collections.abc import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent


async def list_sign_in_events(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    limit: int,
    before_id: uuid.UUID | None,
    result: SignInResult | None,
    auth_method: AuthMethod | None,
) -> Sequence[SignInEvent]:
    query = select(SignInEvent).where(SignInEvent.organization_id == organization_id)
    if result is not None:
        query = query.where(SignInEvent.result == result)
    if auth_method is not None:
        query = query.where(SignInEvent.auth_method == auth_method)

    if before_id is not None:
        anchor = (
            await db.execute(
                select(SignInEvent.created_at, SignInEvent.id).where(
                    SignInEvent.id == before_id, SignInEvent.organization_id == organization_id
                )
            )
        ).first()
        if anchor is not None:
            query = query.where(tuple_(SignInEvent.created_at, SignInEvent.id) < anchor)

    query = query.order_by(SignInEvent.created_at.desc(), SignInEvent.id.desc()).limit(limit)
    result_rows = await db.execute(query)
    return result_rows.scalars().all()
```

- [ ] **Step 4: Update the router**

Rewrite `backend/app/routers/sign_in_events.py`:

```python
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import require_org_admin
from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent
from app.models.user import User
from app.repositories.sign_in_events import list_sign_in_events

router = APIRouter(prefix="/sign-in-events", tags=["sign-in-events"])


def _event_out(event: SignInEvent) -> dict:
    return {
        "id": str(event.id),
        "created_at": event.created_at.isoformat(),
        "result": event.result.value,
        "auth_method": event.auth_method.value,
        "email": event.attempted_email,
        "failure_reason": event.failure_reason,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
    }


@router.get("")
async def list_sign_in_events_route(
    limit: int = Query(50, ge=1, le=200),
    before_id: uuid.UUID | None = Query(None),
    result: SignInResult | None = Query(None),
    auth_method: AuthMethod | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    events = await list_sign_in_events(
        db, user.organization_id, limit=limit, before_id=before_id, result=result, auth_method=auth_method
    )
    return {"events": [_event_out(e) for e in events], "has_more": len(events) == limit}
```

Note: the route function is renamed `list_sign_in_events_route` to avoid shadowing the imported repository function `list_sign_in_events` — FastAPI only cares about the decorator, not the function name, so this is safe.

- [ ] **Step 5: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_sign_in_events.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/sign_in_events.py backend/app/repositories/sign_in_events.py backend/tests/routers/test_sign_in_events.py
git commit -m "Extract sign_in_events repository layer"
```

---

### Task 7: `onboarding.py`

**Files:**
- Modify: `backend/app/routers/onboarding.py`
- Create: `backend/tests/routers/test_onboarding.py`

No new repository file — this router only reuses functions from Tasks 2-4. No Pydantic models, no schema extraction.

- [ ] **Step 1: Write `test_onboarding.py` against the current router**

```python
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus

from tests.conftest import login_as, seed_org_and_user


async def test_onboarding_status_fresh_org_local_auth(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body["has_domain"] is False
    assert body["has_verified_domain"] is False
    assert body["has_mailbox"] is True  # local-auth org: hosted mailbox always available


async def test_onboarding_status_fresh_org_entra(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/onboarding/status")

    assert response.status_code == 200
    assert response.json()["has_mailbox"] is False  # entra org, no MailboxConnection yet


async def test_onboarding_status_with_verified_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified))
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body["has_domain"] is True
    assert body["has_verified_domain"] is True
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_onboarding.py -v`
Expected: 3 passed.

- [ ] **Step 3: Update the router to reuse the repository functions from Tasks 2-4**

Rewrite `backend/app/routers/onboarding.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.repositories.dmarc_reports import count_reports_for_org
from app.repositories.dns_checks import count_dns_checks_for_org
from app.repositories.domains import count_domains_for_org, count_verified_domains_for_org
from app.repositories.mailbox_connections import get_org_mailbox_connection
from app.repositories.organizations import get_organization
from app.models.user import User

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/status")
async def onboarding_status(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    org = await get_organization(db, user.organization_id)
    connection = await get_org_mailbox_connection(db, user.organization_id)

    has_domain = await count_domains_for_org(db, user.organization_id) > 0
    has_verified_domain = await count_verified_domains_for_org(db, user.organization_id) > 0
    has_dns_baseline = await count_dns_checks_for_org(db, user.organization_id) > 0
    has_any_report = await count_reports_for_org(db, user.organization_id) > 0

    has_mailbox = connection is not None or (org is not None and org.entra_tenant_id is None)

    return {
        "org_name": org.name if org is not None else None,
        "user_role": user.role.value,
        "has_mailbox": has_mailbox,
        "mailbox_consent_granted": connection is not None and connection.consent_status.value == "granted",
        "mailbox_last_sync_status": (
            connection.last_sync_status.value if connection is not None and connection.last_sync_status else None
        ),
        "has_domain": has_domain,
        "has_verified_domain": has_verified_domain,
        "has_dns_baseline": has_dns_baseline,
        "has_any_report": has_any_report,
    }
```

- [ ] **Step 4: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_onboarding.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/onboarding.py backend/tests/routers/test_onboarding.py
git commit -m "Migrate onboarding router onto the repository layer"
```

---

### Task 8: `action_queue.py`

**Files:**
- Modify: `backend/app/routers/action_queue.py`
- Create: `backend/tests/routers/test_action_queue.py`

No new repository file — reuses Task 2/3 functions. No Pydantic models.

- [ ] **Step 1: Write `test_action_queue.py` against the current router**

```python
from app.models.domain import Domain

from tests.conftest import login_as, seed_org_and_user


async def test_action_queue_empty_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    assert response.json() == []


async def test_action_queue_scoped_to_one_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue", params={"domain_id": str(domain.id)})

    assert response.status_code == 200


async def test_action_queue_unknown_domain_404s(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue", params={"domain_id": "00000000-0000-0000-0000-000000000000"})

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_action_queue.py -v`
Expected: 3 passed.

- [ ] **Step 3: Update the router to reuse the domains/mailbox_connections repository functions**

Rewrite `backend/app/routers/action_queue.py` in full (the only body changes are the two blocks that build `domains` and fetch `connection`; `Domain`, `MailboxConnection`, `HTTPException`, `status`, and `select` all drop out of the imports because this was their only use in the file):

```python
import dataclasses
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.user import User
from app.repositories.domains import get_owned_domain, list_domains_for_org
from app.repositories.mailbox_connections import get_org_mailbox_connection
from app.services.dmarc_analytics import service_breakdown
from app.services.action_queue.rules import (
    domain_ready_for_stricter_policy,
    enforcement_readiness_notice,
    high_volume_failure,
    likely_spoofed_sender,
    low_compliance_domain,
    mailbox_stopped_receiving_reports,
    parked_domain_not_locked_down,
    rua_destination_broken,
    sender_alignment_issue,
    spf_lookup_limit_risk,
    unknown_sender_above_threshold,
)

router = APIRouter(tags=["action-queue"])

_SEVERITY_ORDER = {"critical": 0, "serious": 1, "warning": 2, "good": 3, "neutral": 4}


@router.get("/action-queue")
async def action_queue(
    domain_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    if domain_id is not None:
        domains = [await get_owned_domain(db, domain_id, user.organization_id)]
    else:
        domains = await list_domains_for_org(db, user.organization_id)

    items = list(await mailbox_stopped_receiving_reports(db, user.organization_id))

    if domain_id is None:
        items += await enforcement_readiness_notice(db, domains)

    connection = await get_org_mailbox_connection(db, user.organization_id)
    mailbox_address = connection.mailbox_address if connection is not None else None

    for domain in domains:
        services = await service_breakdown(db, domain.id)
        items += await unknown_sender_above_threshold(db, domain, services)
        items += await likely_spoofed_sender(db, domain, services)
        items += sender_alignment_issue(domain, services)
        items += await domain_ready_for_stricter_policy(db, domain)
        items += await low_compliance_domain(db, domain)
        items += await high_volume_failure(db, domain)
        items += await spf_lookup_limit_risk(db, domain)
        items += await rua_destination_broken(db, domain, mailbox_address)
        items += await parked_domain_not_locked_down(domain)

    await db.commit()

    items.sort(key=lambda i: (i.category, _SEVERITY_ORDER.get(i.severity, 5)))
    return [dataclasses.asdict(i) for i in items]
```

- [ ] **Step 4: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_action_queue.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/action_queue.py backend/tests/routers/test_action_queue.py
git commit -m "Migrate action_queue router onto the repository layer"
```

---

### Task 9: `admin_updates.py`

**Files:**
- Modify: `backend/app/routers/admin_updates.py`
- Create: `backend/app/schemas/admin_updates.py`
- Create: `backend/tests/routers/test_admin_updates.py`

No repository extraction — this router has no direct `select()` calls (it goes through the existing `update_check` service). Schema extraction only. This task also needs a platform-admin login helper, added here since it's the first task that needs one.

- [ ] **Step 1: Add a platform-admin login helper to `conftest.py`**

Append to `backend/tests/conftest.py` (after `login_as`):

```python
async def login_as_platform_admin(client: httpx.AsyncClient, owner_factory) -> None:
    """Creates a local PlatformAdmin and logs the client in as them. password_hash
    is a placeholder, not a real hash — this mints a session directly via
    session_manager, the same bypass a real password login would produce,
    without ever calling verify_password, so the placeholder is never checked."""
    from app.models.platform_admin import PlatformAdmin

    async with owner_factory() as db:
        admin = PlatformAdmin(email="admin@platform.example", password_hash="unused", is_active=True)
        db.add(admin)
        await db.flush()
        _, raw_token = await session_manager.create_platform_admin_session(
            db, platform_admin_id=admin.id, ip_address="127.0.0.1", user_agent="pytest",
        )
        await db.commit()
    client.cookies.set(settings.platform_admin_session_cookie_name, raw_token)
```

- [ ] **Step 2: Write `test_admin_updates.py` against the current router**

```python
from tests.conftest import login_as_platform_admin


async def test_get_update_status_requires_admin(api):
    client, _owner_factory = api
    response = await client.get("/api/admin/updates")
    assert response.status_code == 401


async def test_get_update_status(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/updates")

    assert response.status_code == 200
    assert "running_version" in response.json()


async def test_update_settings(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.patch("/api/admin/updates", json={"include_prereleases": True})

    assert response.status_code == 200
    assert response.json()["include_prereleases"] is True
```

- [ ] **Step 3: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_admin_updates.py -v`
Expected: 3 passed.

- [ ] **Step 4: Extract the schema**

Create `backend/app/schemas/admin_updates.py`:

```python
from pydantic import BaseModel


class UpdateSettingsPatch(BaseModel):
    include_prereleases: bool
```

- [ ] **Step 5: Update the router**

In `backend/app/routers/admin_updates.py`, replace `from pydantic import BaseModel` and the inline `class UpdateSettingsPatch(BaseModel): ...` with `from app.schemas.admin_updates import UpdateSettingsPatch`. Everything else in the file is unchanged.

- [ ] **Step 6: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_admin_updates.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/conftest.py backend/app/routers/admin_updates.py backend/app/schemas/admin_updates.py backend/tests/routers/test_admin_updates.py
git commit -m "Extract admin_updates schema; add platform-admin test login helper"
```

---

### Task 10: `users.py`

**Files:**
- Modify: `backend/app/routers/users.py`
- Create: `backend/app/schemas/users.py`
- Create: `backend/app/repositories/users.py`
- Create: `backend/tests/routers/test_users.py`

- [ ] **Step 1: Write `test_users.py` against the current router**

```python
from app.models.enums import AuthMethod, UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_list_users(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/users")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["email"] == user.email


async def test_create_local_user_requires_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com"})

    assert response.status_code == 403


async def test_create_local_user_rejects_entra_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com"})

    assert response.status_code == 409


async def test_create_local_user(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com", "display_name": "New Person"})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert "setup_link" in body


async def test_update_user_cannot_self_demote(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/users/{user.id}", json={"role": "member"})

    assert response.status_code == 400


async def test_update_user_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    _other_org, other_user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/users/{other_user.id}", json={"role": "org_admin"})

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_users.py -v`
Expected: 6 passed.

- [ ] **Step 3: Extract the schema**

Create `backend/app/schemas/users.py`:

```python
from pydantic import BaseModel, EmailStr

from app.models.enums import UserRole, UserStatus


class UserUpdateRequest(BaseModel):
    role: UserRole | None = None
    status: UserStatus | None = None


class LocalUserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None
    role: UserRole = UserRole.member
```

- [ ] **Step 4: Extract the repository**

Create `backend/app/repositories/users.py`:

```python
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def list_users_for_org(db: AsyncSession, organization_id: UUID) -> Sequence[User]:
    result = await db.execute(select(User).where(User.organization_id == organization_id).order_by(User.email))
    return result.scalars().all()


async def get_user_in_org(db: AsyncSession, user_id: UUID, organization_id: UUID) -> User | None:
    user = await db.get(User, user_id)
    if user is None or user.organization_id != organization_id:
        return None
    return user
```

Note: unlike `get_owned_domain`, this returns `None` rather than raising — the router has two different responses depending on which check fails (404 "user not found" vs. the self-demote 400), so the raise-on-not-found shortcut used for domains doesn't fit here as cleanly; the router keeps its own `if target is None: raise HTTPException(...)`.

- [ ] **Step 5: Update the router**

Rewrite `backend/app/routers/users.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import AuthMethod, UserRole
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.user import User
from app.repositories.users import get_user_in_org, list_users_for_org
from app.schemas.users import LocalUserCreateRequest, UserUpdateRequest
from app.services.auth.tokens import new_opaque_token

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "status": user.status.value,
        "auth_method": user.auth_method.value,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    users = await list_users_for_org(db, user.organization_id)
    return [_user_out(u) for u in users]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_local_user(
    body: LocalUserCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_org_admin),
) -> dict:
    org = await db.get(Organization, admin.organization_id)
    if org is None or org.entra_tenant_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this organization uses Entra SSO — teammates join by signing in, not manual creation"
        )

    new_user = User(
        organization_id=admin.organization_id,
        auth_method=AuthMethod.local,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
    )
    db.add(new_user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a user with this email already exists")

    raw_token, token_hash = new_opaque_token()
    now = datetime.now(timezone.utc)
    db.add(
        PasswordSetupToken(
            user_id=new_user.id,
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(hours=settings.password_setup_token_timeout_hours),
        )
    )
    await db.flush()
    await db.refresh(new_user)
    await db.commit()

    return {**_user_out(new_user), "setup_link": f"{settings.public_base_url}/set-password?token={raw_token}"}


@router.patch("/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_org_admin),
) -> dict:
    target = await get_user_in_org(db, user_id, admin.organization_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if target.id == admin.id and body.role is not None and body.role != UserRole.org_admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot demote yourself")

    if body.role is not None:
        target.role = body.role
    if body.status is not None:
        target.status = body.status

    await db.flush()
    await db.refresh(target)
    await db.commit()
    return _user_out(target)
```

- [ ] **Step 6: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_users.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/users.py backend/app/schemas/users.py backend/app/repositories/users.py backend/tests/routers/test_users.py
git commit -m "Extract users schema/repository layer"
```

---

### Task 11: `main.py` middleware split

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/app/middleware/security_headers.py`
- Create: `backend/app/middleware/csrf.py`
- Create: `backend/app/middleware/demo_read_only.py`
- Create: `backend/app/middleware/mta_sts_routing.py`
- Create: `backend/app/middleware/spa_static.py`
- Create: `backend/tests/routers/test_main.py`

**Interfaces:**
- Consumes: `api` fixture (Task 1).

- [ ] **Step 1: Write `test_main.py` against the current `main.py`**

```python
from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_health_endpoint(api):
    client, _owner_factory = api
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_security_headers_present(api):
    client, _owner_factory = api
    response = await client.get("/api/health")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_csrf_header_required_for_post(api):
    client, _owner_factory = api
    response = await client.post("/api/auth/logout", headers={"X-Requested-With": ""})
    assert response.status_code == 403


async def test_demo_read_only_blocks_mutation(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, is_demo_read_only=True)
    await login_as(client, owner_factory, user)

    response = await client.patch("/api/organizations/current", json={"name": "Renamed"})

    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]
```

Note: `test_csrf_header_required_for_post` sends an explicitly-empty header rather than omitting it, because the `api` fixture's `httpx.AsyncClient` has `CSRF_HEADERS` set as default headers — per-request `headers=` on `httpx` merges with, rather than replaces, client-level defaults unless the same key is set to override it, and setting it to `""` here does override it to a value the middleware rejects, exercising the same 403 path as a client that never sent the header at all.

- [ ] **Step 2: Run tests, confirm pass**

Run: `cd backend && pytest tests/routers/test_main.py -v`
Expected: 4 passed.

- [ ] **Step 3: Extract each middleware into its own module**

Create `backend/app/middleware/security_headers.py`:

```python
from fastapi import Request

from app.config import settings

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'",
}


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if settings.public_base_url.startswith("https://"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
```

Create `backend/app/middleware/csrf.py`:

```python
from fastapi import Request
from fastapi.responses import JSONResponse

CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "yetanotherdmarctool"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def enforce_csrf_header(request: Request, call_next):
    if (
        request.method in UNSAFE_METHODS
        and request.url.path.startswith("/api/")
        and request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE
    ):
        return JSONResponse({"detail": "missing or invalid X-Requested-With header"}, status_code=403)
    return await call_next(request)
```

Create `backend/app/middleware/demo_read_only.py` (docstring copied verbatim from the current `main.py` — it documents a real, non-obvious gotcha and must not be lost):

```python
from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import async_session_factory
from app.models.organization import Organization
from app.services.auth import session_manager

from .csrf import UNSAFE_METHODS


async def enforce_demo_read_only(request: Request, call_next):
    """Organizations flagged is_demo_read_only (see the Organization model
    — the one intended use is a published public demo login) can't perform
    any state-changing action. /api/auth/ is exempt so a demo visitor can
    still log in/out/enroll TOTP etc.; everything else under /api/ with an
    unsafe method is blocked.

    /api/admin/ is also exempt — platform-admin auth is a completely
    separate realm from the org-scoped session this check keys off of
    (session_cookie_name, not platform_admin_session_cookie_name), so it
    was never meant to be in scope here. Without this, a stray regular
    dmarc_session cookie left over from ever trying the public demo login
    in the same browser blocks even POST /api/admin/login itself, purely
    because that leftover cookie happens to resolve to the read-only demo
    org — confirmed live: the platform admin couldn't log into their own
    demo instance's admin console because of an unrelated cookie.

    Checked here at the middleware level — not only inside get_current_user/
    require_org_admin — as defense in depth: coverage this way doesn't
    depend on every current and future mutating route correctly using
    those dependencies, the same reasoning restrict_mta_sts_hostname above
    is checked at this layer rather than per-route."""
    if (
        request.method in UNSAFE_METHODS
        and request.url.path.startswith("/api/")
        and not request.url.path.startswith("/api/auth/")
        and not request.url.path.startswith("/api/admin/")
    ):
        raw_token = request.cookies.get(settings.session_cookie_name)
        if raw_token:
            async with async_session_factory() as db:
                session = await session_manager.get_active_user_session(db, raw_token)
                if session is not None:
                    await set_org_context(db, session.organization_id)
                    org = await db.get(Organization, session.organization_id)
                    if org is not None and org.is_demo_read_only:
                        return JSONResponse(
                            {"detail": "This is a read-only public demo — changes aren't saved."},
                            status_code=status.HTTP_403_FORBIDDEN,
                        )
    return await call_next(request)
```

Create `backend/app/middleware/mta_sts_routing.py`:

```python
from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.config import settings


async def restrict_mta_sts_hostname(request: Request, call_next):
    """If this instance serves its own MTA-STS policy (mta_sts_policy_* in
    config.py), that hostname must serve *only* that one file — a reverse
    proxy pointed at this same app for mta-sts.<domain> would otherwise
    also expose the full dashboard/login page and API there too, since
    nothing else in this app routes by Host header. 404s everything else
    on that exact Host rather than falling through to the SPA/API."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if (
        settings.mta_sts_policy_hostname
        and host == settings.mta_sts_policy_hostname
        and request.url.path != "/.well-known/mta-sts.txt"
    ):
        return JSONResponse(
            {"detail": "not found"}, status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": "no-store"}
        )
    return await call_next(request)
```

Create `backend/app/middleware/spa_static.py`:

```python
from pathlib import Path

from fastapi.responses import FileResponse

NO_STORE = {"Cache-Control": "no-store"}


def make_serve_spa(static_dir: Path):
    async def serve_spa(full_path: str) -> FileResponse:
        root = static_dir.resolve()
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate, headers=NO_STORE)
        return FileResponse(root / "index.html", headers=NO_STORE)

    return serve_spa
```

Note: `serve_spa` is wrapped in a factory (`make_serve_spa`) rather than being a bare module-level function, because it closes over `STATIC_DIR` — this keeps `app/middleware/spa_static.py` itself free of any module-level state tied to a specific app instance, consistent with the Global Constraints' statelessness rule (the closure captures a constant `Path`, not mutable state, but the factory pattern makes that explicit rather than relying on a module-level import-time constant).

- [ ] **Step 4: Update `main.py` to wire in the extracted middlewares**

Rewrite `backend/app/main.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.middleware.csrf import enforce_csrf_header
from app.middleware.demo_read_only import enforce_demo_read_only
from app.middleware.mta_sts_routing import restrict_mta_sts_hostname
from app.middleware.security_headers import add_security_headers
from app.middleware.spa_static import make_serve_spa
from app.routers import (
    action_queue,
    admin_updates,
    auth,
    dmarc_reports,
    dns_checks,
    domains,
    mailbox_connections,
    onboarding,
    organizations,
    platform_admin,
    selectors,
    sign_in_events,
    users,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="YetAnotherDmarcTool API")

app.middleware("http")(add_security_headers)
app.middleware("http")(enforce_csrf_header)
app.middleware("http")(enforce_demo_read_only)
app.middleware("http")(restrict_mta_sts_hostname)

api_router = APIRouter(prefix="/api")


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment, "version": settings.app_version}


api_router.include_router(auth.router)
api_router.include_router(platform_admin.router)
api_router.include_router(organizations.router)
api_router.include_router(domains.router)
api_router.include_router(users.router)
api_router.include_router(mailbox_connections.router)
api_router.include_router(dmarc_reports.router)
api_router.include_router(selectors.router)
api_router.include_router(dns_checks.router)
api_router.include_router(action_queue.router)
api_router.include_router(onboarding.router)
api_router.include_router(sign_in_events.router)
api_router.include_router(admin_updates.router)

app.include_router(api_router)


@app.get("/.well-known/security.txt")
async def security_txt() -> PlainTextResponse:
    """RFC 9116. 404s entirely if security_contact_email isn't configured —
    the RFC requires at least one Contact field, so there's nothing correct
    to serve without it (see the setting's own docstring in config.py).
    Expires is computed fresh each request (now + 1 year) rather than a
    hardcoded date, so this never silently goes stale from being forgotten."""
    if not settings.security_contact_email:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    expires = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = (
        f"Contact: mailto:{settings.security_contact_email}\n"
        f"Expires: {expires}\n"
        "Preferred-Languages: en\n"
        f"Canonical: {settings.public_base_url}/.well-known/security.txt\n"
    )
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/.well-known/mta-sts.txt")
async def mta_sts_policy(request: Request) -> PlainTextResponse:
    """This instance's own MTA-STS policy (see mta_sts_policy_* in
    config.py) — 404s unless both are configured AND the request's Host
    header matches exactly, since this same app also answers on its own
    dashboard hostname, which isn't what this policy is for."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if not settings.mta_sts_policy_hostname or not settings.mta_sts_policy_body or host != settings.mta_sts_policy_hostname:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return PlainTextResponse(
        settings.mta_sts_policy_body, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"}
    )


if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="spa-assets")

    app.get("/{full_path:path}")(make_serve_spa(STATIC_DIR))
```

- [ ] **Step 5: Run tests, confirm still passing**

Run: `cd backend && pytest tests/routers/test_main.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py backend/app/middleware/security_headers.py backend/app/middleware/csrf.py backend/app/middleware/demo_read_only.py backend/app/middleware/mta_sts_routing.py backend/app/middleware/spa_static.py backend/tests/routers/test_main.py
git commit -m "Split main.py's inline middlewares into app/middleware/ modules"
```

---

### Task 12: Document the `services/` and `repositories/` organization rules

**Files:**
- Create: `backend/app/services/README.md`
- Create: `backend/app/repositories/README.md`

Per the spec (section 5), `backend/app/services/` currently mixes flat single-purpose files with 11 feature subpackages with no stated rule for the split. The investigation behind the spec found this was undocumented convention, not a wrong outcome — the existing flat files (`dmarc_analytics.py`, `dmarc_narrative.py`, `update_check.py`, `updater_client.py`) are each genuinely one file's worth of code, so nothing needs to move. This task only writes the rule down so it stops being tribal knowledge. No code changes, no tests needed — this is documentation only.

- [ ] **Step 1: Write `backend/app/services/README.md`**

```markdown
# Services organization rule

A service becomes its own subpackage (`services/<name>/` with multiple files) once
it needs 2+ files to express — e.g. `dns_checks/` (17 files), `auth/`, `jobs/`.

A service that's genuinely one file's worth of logic stays flat at `services/`
root — e.g. `dmarc_analytics.py`, `dmarc_narrative.py`, `update_check.py`,
`updater_client.py`. Don't split a flat file into a subpackage just to "match"
the others; only graduate it when it actually grows a second file.
```

- [ ] **Step 2: Write `backend/app/repositories/README.md`**

```markdown
# Repositories organization rule

One file per model/domain area, mirroring `app/models/` (e.g. `repositories/domains.py`
for the `Domain` model, `repositories/dmarc_reports.py` for the two
`DmarcAggregateReport`/`DmarcAggregateRecord` models). This is the only place
`select()`/other read queries against that model should live — routers and
services call these functions rather than querying directly.

Every function takes `db: AsyncSession` as its first argument and returns data
(or raises the standard `HTTPException` for an ownership/not-found check, e.g.
`domains.get_owned_domain`). No module-level caches, singletons, or other
process-local state — the API must stay safe to run as N stateless replicas
(see the horizontal-scale-out work on `v0.1.4-beta`).
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/README.md backend/app/repositories/README.md
git commit -m "Document services/ and repositories/ organization rules"
```

---

### Task 13: Final verification pass

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend test suite**

Run: `cd backend && pytest -v`
Expected: every test passes, including `test_rls.py`, `test_job_queue.py`, `test_leader.py`, `test_rate_limit.py`, the pre-existing `tests/services/` suite, and all 11 new `tests/routers/test_*.py` files from this plan (organizations, domains, mailbox_connections, dns_checks, selectors, sign_in_events, onboarding, action_queue, admin_updates, users, main).

- [ ] **Step 2: Full container build gate**

Run: `docker compose build api worker migrate`
Expected: all three images build cleanly (per `dmarcwatch-verification-discipline` memory, this is the real build gate — it catches issues `pytest` alone won't, like import errors only surfaced at container build time).

- [ ] **Step 3: Live smoke check**

Run: `docker compose up -d api worker` then hit `curl http://localhost:8000/api/health` (adjust port/host to match your local `.env`).
Expected: `{"status": "ok", ...}`.

- [ ] **Step 4: Confirm no stray duplication remains**

Run: `grep -rn "_get_owned_domain\b" backend/app/routers/`
Expected: no matches — the triplicated helper (`dns_checks.py`, `dmarc_reports.py`, `selectors.py`) should be fully gone from these 11 routers. (`dmarc_reports.py` still has its own local copy — that's expected, it's out of this plan's scope and will be handled in Plan A2.)

- [ ] **Step 5: Commit the final state if step 4 needed any cleanup**

If step 4 found leftover references, fix them, re-run the full suite, and commit:

```bash
git add -A
git commit -m "Clean up remaining _get_owned_domain references"
```

Otherwise, this task needs no commit — it's a verification checkpoint, not a code change.
