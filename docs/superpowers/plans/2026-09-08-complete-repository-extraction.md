# Complete the Repository-Layer Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the cohesion-refactor spec's repository-layer goal for real this time. The previous plan (`2026-09-07-services-repository-extraction.md`) claimed "routers and services call repository functions; neither issues raw queries directly anymore" — that claim was false, caught only by its own final review: 14 more files across `app/workers/`, `app/services/{dns_checks,ingestion,retention,auth}/`, and `app/scripts/` still issue ~30 inline queries directly. This plan is the result of an exhaustive, file-by-file sweep of the **entire** `backend/app/` tree (not just a guess at which files might qualify), and covers every one of them. Nothing found in that sweep is deferred out of this plan.

**Architecture:** Same extraction pattern as every prior plan in this sequence: each inline query moves into the repository file for the model/feature area it queries; the calling function keeps its exact name, signature, and every caller, only its internal implementation changes. Two things apply across this whole plan that didn't come up as strongly before:

1. **This sweep found 6 more genuine duplicate queries** — call sites that already match an existing repository function exactly (three of them duplicate the very functions the *previous* plan just created or reused). Every task below identifies its duplicates explicitly; reuse, never reimplement.
2. **Two categories of `db` access are deliberately out of this plan's scope, on architectural grounds, not laziness — read this before assuming a gap:**
   - **`app/db/rls.py`** (`set_org_context`/`set_platform_admin_context`) issues raw `db.execute(text("SELECT set_config(...)"))` calls. These are transaction/session configuration, not data access — nothing to query, nothing to deduplicate, no domain model involved. Moving this into a repository would misclassify what a repository is for (fetching/writing business data). Left as-is.
   - **Bare `db.get(Model, id)` primary-key lookups** (e.g. `await db.get(Domain, domain.parent_domain_id)` in `scheduled_recheck.py`, `await db.get(Organization, ...)` in the same file) are excluded throughout this plan. A `db.get()` call already IS the simplest, most centralized form of data access there is — wrapping it in a repository function that does nothing but `return await db.get(Model, id)` adds indirection with no query logic to deduplicate or centralize. Every task below explicitly lists which `db.get()` calls in its files are being left alone.

If you (the implementer or reviewer) find inline SQL this plan's sweep missed, or a query that doesn't fit the two exclusions above and isn't covered by any task, that is a plan defect — ledger it and rule on it per the subagent-driven-development process; do not silently work around it or leave it for "later." There is no "later" after this plan.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Postgres/RLS (unchanged). Direct repository-level tests for every new function, following the established `tests/repositories/` convention.

**Spec:** `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`

## Global Constraints

- Every repository function takes `db: AsyncSession` as its first argument, returns data, and holds no module-level mutable state.
- Every touched service/worker/script function keeps its existing name, signature, and all existing callers unchanged — only its internal implementation changes. This is a pure behavior-preserving move.
- Repository functions never import types defined under `app/services/`.
- Preserve every existing inline comment when moving code verbatim, not paraphrased or truncated — this codebase's non-obvious rationale (RFC citations, race-condition notes, idempotency/dedup reasoning) has to survive every move. Several files in this plan (`report_writer.py`, `mailbox_poll_job.py`) have unusually dense, load-bearing comments — read them fully before touching anything.
- Before creating a new repository function, check whether an existing one — including ones added earlier in THIS plan — already does the exact same query. This plan names every duplicate its own sweep found; a task's own new functions must also be checked against every repository file that already exists, not just the ones this plan touches.
- One exception to "never change an existing function's signature," used once in Task 4: `list_unmatched_aggregate_reports`'s `limit: int` parameter becomes `limit: int | None = None`. This is a strictly additive, backward-compatible widening — the one existing caller (the `/dmarc/unmatched` router endpoint) always passes an explicit value and is unaffected; the new caller in this plan passes `None` to mean "no limit." This is the only signature change this plan makes anywhere.
- Security-sensitive files (`session_manager.py` in Task 8) require exact behavioral preservation of timing/fail-closed semantics — moving the query must not change what gets compared, when, or how errors propagate.

---

### Task 1: Foundational repository additions + `bootstrap_platform_admin.py`

**Files:**
- Modify: `backend/app/repositories/organizations.py` (add one function)
- Modify: `backend/app/repositories/platform_admin.py` (add one function)
- Modify: `backend/app/scripts/bootstrap_platform_admin.py`
- Create: `backend/tests/repositories/test_organizations.py`, add to `backend/tests/repositories/test_platform_admin.py` (create if it doesn't exist)

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.repositories.organizations.get_org_by_demo_flag(db) -> Organization | None` (consumed by Task 11's scripts), `app.repositories.platform_admin.count_platform_admins(db) -> int` (consumed only by this task).

- [ ] **Step 1: Add `get_org_by_demo_flag` to `backend/app/repositories/organizations.py`**

```python
from sqlalchemy import select
```

Add this import alongside the existing ones, then:

```python
async def get_org_by_demo_flag(db: AsyncSession) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.is_demo_read_only.is_(True)))
    return result.scalar_one_or_none()
```

Do not touch the existing `get_organization` function.

- [ ] **Step 2: Add `count_platform_admins` to `backend/app/repositories/platform_admin.py`**

Read the file first to see its existing imports (`func`/`select` are likely already imported given `org_aggregates`/`job_runs_summary_stats` exist there — verify before adding a duplicate import).

```python
async def count_platform_admins(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(PlatformAdmin))
    return result.scalar_one()
```

`PlatformAdmin` needs importing from `app.models.platform_admin` if not already present in this file.

- [ ] **Step 3: Migrate `backend/app/scripts/bootstrap_platform_admin.py`**

```python
from app.repositories.platform_admin import count_platform_admins
```

Replace:

```python
        result = await db.execute(select(func.count()).select_from(PlatformAdmin))
        existing_count = result.scalar_one()
```

with:

```python
        existing_count = await count_platform_admins(db)
```

Remove `from sqlalchemy import func, select` and the `PlatformAdmin` import if nothing else in this tiny script still needs them (check — `PlatformAdmin(...)` is still constructed further down in the same function, so that import stays; `func`/`select` become unused, remove them).

- [ ] **Step 4: Write repository tests**

```python
# backend/tests/repositories/test_organizations.py
from app.models.organization import Organization
from app.repositories.organizations import get_org_by_demo_flag

from tests.conftest import seed_org_and_user


async def test_get_org_by_demo_flag_returns_none_when_no_demo_org(api):
    _client, owner_factory = api
    await seed_org_and_user(owner_factory)

    async with owner_factory() as db:
        result = await get_org_by_demo_flag(db)

    assert result is None


async def test_get_org_by_demo_flag_finds_the_demo_org(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        from app.models.enums import OrganizationStatus

        org = Organization(name="Demo", status=OrganizationStatus.active, is_demo_read_only=True)
        db.add(org)
        await db.commit()
        await db.refresh(org)

    async with owner_factory() as db:
        result = await get_org_by_demo_flag(db)

    assert result is not None
    assert result.id == org.id
```

If `backend/tests/repositories/test_platform_admin.py` doesn't exist yet, create it with:

```python
# backend/tests/repositories/test_platform_admin.py
from app.repositories.platform_admin import count_platform_admins


async def test_count_platform_admins_zero_initially(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        result = await count_platform_admins(db)
    assert result == 0
```

(If the file already exists with other tests, just add this one test function to it — do not duplicate existing tests.)

- [ ] **Step 5: Run tests**

Run: `pytest tests/repositories/test_organizations.py tests/repositories/test_platform_admin.py -v`, then the full suite.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/organizations.py backend/app/repositories/platform_admin.py backend/app/scripts/bootstrap_platform_admin.py backend/tests/repositories/
git commit -m "Move bootstrap_platform_admin.py's query onto the repository layer"
```

---

### Task 2: `backend/app/repositories/domains.py` extensions + `domain_verification.py`

**Files:**
- Modify: `backend/app/repositories/domains.py` (add 5 functions)
- Modify: `backend/app/services/dns_checks/domain_verification.py`
- Modify: `backend/tests/repositories/test_domains.py` (create if it doesn't exist)

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.repositories.domains.get_domain_id_by_org_and_name(db, organization_id, name) -> UUID | None` (consumed by Task 3), `.get_domain_by_org_and_name(db, organization_id, name) -> Domain | None` (consumed by Task 11), `.list_domains_with_hosted_report_address(db) -> Sequence[Domain]` (consumed by Task 6), `.list_pending_domains(db) -> Sequence[Domain]` (consumed only by this task), `.mark_pending_subdomains_verified(db, parent_domain_id, verified_at) -> None` (consumed only by this task).

- [ ] **Step 1: Read `backend/app/repositories/domains.py` in full first**

Note its existing imports and the exact style of `get_owned_domain`/`list_domains_for_org` before adding to it.

- [ ] **Step 2: Add the five functions**

```python
async def get_domain_id_by_org_and_name(db: AsyncSession, organization_id: UUID, name: str) -> UUID | None:
    result = await db.execute(select(Domain.id).where(Domain.organization_id == organization_id, Domain.name == name))
    return result.scalar_one_or_none()


async def get_domain_by_org_and_name(db: AsyncSession, organization_id: UUID, name: str) -> Domain | None:
    result = await db.execute(select(Domain).where(Domain.organization_id == organization_id, Domain.name == name))
    return result.scalar_one_or_none()


async def list_domains_with_hosted_report_address(db: AsyncSession) -> Sequence[Domain]:
    result = await db.execute(select(Domain).where(Domain.hosted_report_address.is_not(None)))
    return result.scalars().all()


async def list_pending_domains(db: AsyncSession) -> Sequence[Domain]:
    result = await db.execute(select(Domain).where(Domain.verification_status == DomainVerificationStatus.pending))
    return result.scalars().all()


async def mark_pending_subdomains_verified(db: AsyncSession, parent_domain_id: UUID, verified_at: datetime) -> None:
    """Propagates verification to any still-pending subdomains of an apex
    that just got verified — see apply_domain_verification in
    app/services/dns_checks/domain_verification.py."""
    await db.execute(
        Domain.__table__.update()
        .where(Domain.parent_domain_id == parent_domain_id, Domain.verification_status == DomainVerificationStatus.pending)
        .values(verification_status=DomainVerificationStatus.verified, verified_at=verified_at)
    )
```

New imports needed: `from datetime import datetime`, `DomainVerificationStatus` from `app.models.enums` — check both aren't already imported before adding.

- [ ] **Step 3: Migrate `backend/app/services/dns_checks/domain_verification.py`**

Add `from app.repositories.domains import list_pending_domains, mark_pending_subdomains_verified`.

In `apply_domain_verification`, replace:

```python
    if domain.parent_domain_id is None:
        await db.execute(
            Domain.__table__.update()
            .where(
                Domain.parent_domain_id == domain.id,
                Domain.verification_status == DomainVerificationStatus.pending,
            )
            .values(verification_status=DomainVerificationStatus.verified, verified_at=now)
        )
    return True
```

with:

```python
    if domain.parent_domain_id is None:
        await mark_pending_subdomains_verified(db, domain.id, now)
    return True
```

In `run_domain_verification_sweep`, replace:

```python
        pending = (
            (await db.execute(select(Domain).where(Domain.verification_status == DomainVerificationStatus.pending)))
            .scalars()
            .all()
        )
```

with:

```python
        pending = await list_pending_domains(db)
```

Remove `from sqlalchemy import select` if nothing else in this file uses it (check — it shouldn't, this file only had the one query). Keep `Domain`/`DomainVerificationStatus` imports if the type annotations in this file still reference them (they do — `domain: Domain` parameter, etc.).

- [ ] **Step 4: Write repository tests**

```python
# backend/tests/repositories/test_domains.py (add these if the file already exists from an earlier plan; check first)
import uuid
from datetime import datetime, timezone

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.repositories.domains import (
    get_domain_by_org_and_name,
    get_domain_id_by_org_and_name,
    list_domains_with_hosted_report_address,
    list_pending_domains,
    mark_pending_subdomains_verified,
)

from tests.conftest import seed_org_and_user


async def _add_domain(owner_factory, org, **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_get_domain_id_by_org_and_name_found_and_not_found(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org, name="example.com")

    async with owner_factory() as db:
        found = await get_domain_id_by_org_and_name(db, org.id, "example.com")
        missing = await get_domain_id_by_org_and_name(db, org.id, "nope.example")

    assert found == domain.id
    assert missing is None


async def test_get_domain_by_org_and_name_returns_full_object(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org, name="example.com")

    async with owner_factory() as db:
        result = await get_domain_by_org_and_name(db, org.id, "example.com")

    assert result is not None
    assert result.name == "example.com"


async def test_list_domains_with_hosted_report_address(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org, name="a.example", hosted_report_address="a@reports.example")
    await _add_domain(owner_factory, org, name="b.example")

    async with owner_factory() as db:
        results = await list_domains_with_hosted_report_address(db)

    names = {d.name for d in results}
    assert "a.example" in names
    assert "b.example" not in names


async def test_list_pending_domains(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org, name="pending.example", verification_status=DomainVerificationStatus.pending)
    await _add_domain(owner_factory, org, name="verified.example", verification_status=DomainVerificationStatus.verified)

    async with owner_factory() as db:
        results = await list_pending_domains(db)

    names = {d.name for d in results}
    assert "pending.example" in names
    assert "verified.example" not in names


async def test_mark_pending_subdomains_verified(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    parent = await _add_domain(owner_factory, org, name="example.com", verification_status=DomainVerificationStatus.verified)
    sub_pending = await _add_domain(
        owner_factory, org, name="sub.example.com", parent_domain_id=parent.id,
        verification_status=DomainVerificationStatus.pending,
    )
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        await mark_pending_subdomains_verified(db, parent.id, now)
        await db.commit()

    async with owner_factory() as db:
        refreshed = await db.get(Domain, sub_pending.id)
    assert refreshed.verification_status == DomainVerificationStatus.verified
    assert refreshed.verified_at is not None
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/repositories/test_domains.py tests/routers/test_domains.py -v`, then the full suite.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/domains.py backend/app/services/dns_checks/domain_verification.py backend/tests/repositories/test_domains.py
git commit -m "Extend repositories/domains.py; move domain_verification.py's queries onto it"
```

---

### Task 3: `domain_matcher.py`

**Files:**
- Modify: `backend/app/services/ingestion/domain_matcher.py`

**Interfaces:**
- Consumes: `app.repositories.domains.get_domain_id_by_org_and_name` (Task 2).
- Produces: nothing new.

- [ ] **Step 1: Migrate `match_domain`**

Add `from app.repositories.domains import get_domain_id_by_org_and_name`. The ancestor-walk loop (real business logic) stays exactly as-is — only the per-iteration query changes:

```python
async def match_domain(db: AsyncSession, organization_id: uuid.UUID, published_domain: str) -> uuid.UUID | None:
    """Resolves a report's policy_published/reported domain to a registered
    Domain row: exact match first, then walk up to the closest registered
    ancestor (e.g. a report published for "mail.sub.example.com" matches a
    registered apex "example.com" even though the exact subdomain wasn't
    separately added). Returns None if nothing matches — the caller persists
    the report with domain_id=NULL into the "unmatched" bucket rather than
    dropping it."""
    published_domain = published_domain.strip().lower().rstrip(".")

    labels = published_domain.split(".")
    for start in range(len(labels) - 1):
        candidate = ".".join(labels[start:])
        domain_id = await get_domain_id_by_org_and_name(db, organization_id, candidate)
        if domain_id is not None:
            return domain_id

    return None
```

Remove `from sqlalchemy import select` and `AsyncSession` stays (still used in the type hint). `Domain` model import becomes unused — remove it if nothing else in this tiny file references it.

- [ ] **Step 2: Run tests**

This function has no direct test file today — it's exercised indirectly through `report_writer.py`'s tests (which don't exist as direct tests either — see Task 4) and through the `/domains` router's resweep call sites. Run: `pytest tests/routers/test_domains.py tests/repositories/test_domains.py -v`, then the full suite.
Expected: all pass, no regressions (this is a pure internal refactor of a 2-line loop body).

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/ingestion/domain_matcher.py
git commit -m "Move domain_matcher.py's query onto the repository layer"
```

---

### Task 4: `report_writer.py` (the largest task in this plan)

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add 6 functions, widen 1 existing signature)
- Modify: `backend/app/services/ingestion/report_writer.py`
- Create: `backend/tests/repositories/test_dmarc_reports_ingestion.py`

**Interfaces:**
- Consumes: nothing new from other tasks in this plan.
- Produces: `app.repositories.dmarc_reports.insert_aggregate_report_if_new(db, report: DmarcAggregateReport) -> bool`, `.insert_forensic_report_if_new(db, report: DmarcForensicReport) -> bool`, `.insert_tls_rpt_report_if_new(db, report: TlsRptReport) -> bool`, `.list_unmatched_forensic_reports_for_org(db, organization_id) -> Sequence[DmarcForensicReport]`, `.list_unmatched_tls_rpt_reports_for_org(db, organization_id) -> Sequence[TlsRptReport]`, `.distinct_header_froms_for_domain_or_descendants(db, organization_id, domain_name) -> Sequence[str]`, `.update_record_domain_id_for_header_from(db, organization_id, header_from, domain_id) -> int` — all consumed only within this task. Also widens the EXISTING `list_unmatched_aggregate_reports(db, organization_id, limit)` to `list_unmatched_aggregate_reports(db, organization_id, limit=None)` (see Global Constraints) and reuses it here with `limit=None`.

- [ ] **Step 1: Read `backend/app/services/ingestion/report_writer.py` in full again before starting**

This file's comments document real, previously-confirmed-live bugs (idempotency via `db.begin_nested()`/`IntegrityError`, the RFC 7489 §7.2 header_from-vs-policy_published distinction). Every one of these must survive the move.

- [ ] **Step 2: Widen `list_unmatched_aggregate_reports`'s signature**

In `backend/app/repositories/dmarc_reports.py`, change:

```python
async def list_unmatched_aggregate_reports(
    db: AsyncSession, organization_id: UUID, limit: int
) -> Sequence[DmarcAggregateReport]:
```

to:

```python
async def list_unmatched_aggregate_reports(
    db: AsyncSession, organization_id: UUID, limit: int | None = None
) -> Sequence[DmarcAggregateReport]:
```

and change its body's `.limit(limit)` to only apply when `limit` is not `None`:

```python
    query = (
        select(DmarcAggregateReport)
        .where(DmarcAggregateReport.organization_id == organization_id, DmarcAggregateReport.domain_id.is_(None))
        .order_by(DmarcAggregateReport.received_at.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    result = await db.execute(query)
```

Verify the existing caller in `backend/app/routers/dmarc_reports.py` (the `/dmarc/unmatched` endpoint) still passes an explicit `limit` value and is therefore unaffected — read that call site to confirm before moving on.

- [ ] **Step 3: Add the six new functions to `backend/app/repositories/dmarc_reports.py`**

New imports needed: `from sqlalchemy.exc import IntegrityError`, `from sqlalchemy import or_` (extend the existing `from sqlalchemy import case, func, select, tuple_` line).

```python
async def insert_aggregate_report_if_new(db: AsyncSession, report: DmarcAggregateReport) -> bool:
    """Returns True if newly written, False if this exact report was already
    ingested (natural key: organization + org_name + report_id + published domain,
    per RFC 7489's own dedup guidance). Caller builds the fully-populated
    `report` object (including its DmarcAggregateRecord children, added via
    db.add_all separately) — this function only owns the idempotent insert."""
    try:
        async with db.begin_nested():
            db.add(report)
            await db.flush()
    except IntegrityError:
        return False
    return True


async def insert_forensic_report_if_new(db: AsyncSession, report: DmarcForensicReport) -> bool:
    try:
        async with db.begin_nested():
            db.add(report)
            await db.flush()
    except IntegrityError:
        return False
    return True


async def insert_tls_rpt_report_if_new(db: AsyncSession, report: TlsRptReport) -> bool:
    try:
        async with db.begin_nested():
            db.add(report)
            await db.flush()
    except IntegrityError:
        return False
    return True


async def list_unmatched_forensic_reports_for_org(db: AsyncSession, organization_id: UUID) -> Sequence[DmarcForensicReport]:
    result = await db.execute(
        select(DmarcForensicReport).where(
            DmarcForensicReport.organization_id == organization_id, DmarcForensicReport.domain_id.is_(None)
        )
    )
    return result.scalars().all()


async def list_unmatched_tls_rpt_reports_for_org(db: AsyncSession, organization_id: UUID) -> Sequence[TlsRptReport]:
    result = await db.execute(
        select(TlsRptReport).where(TlsRptReport.organization_id == organization_id, TlsRptReport.domain_id.is_(None))
    )
    return result.scalars().all()


async def distinct_header_froms_for_domain_or_descendants(
    db: AsyncSession, organization_id: UUID, domain_name: str
) -> Sequence[str]:
    """Every distinct header_from value that could possibly be affected by a
    newly-registered domain: itself, or anything ending in '.{domain_name}'
    — nothing else is reachable by match_domain's ancestor walk now that
    this domain exists. See resweep_domain_records in
    app/services/ingestion/report_writer.py."""
    result = await db.execute(
        select(DmarcAggregateRecord.header_from)
        .where(
            DmarcAggregateRecord.organization_id == organization_id,
            or_(
                DmarcAggregateRecord.header_from == domain_name,
                DmarcAggregateRecord.header_from.like(f"%.{domain_name}"),
            ),
        )
        .distinct()
    )
    return result.scalars().all()


async def update_record_domain_id_for_header_from(
    db: AsyncSession, organization_id: UUID, header_from: str, domain_id: UUID | None
) -> int:
    result = await db.execute(
        DmarcAggregateRecord.__table__.update()
        .where(
            DmarcAggregateRecord.organization_id == organization_id,
            DmarcAggregateRecord.header_from == header_from,
            DmarcAggregateRecord.domain_id.is_distinct_from(domain_id),
        )
        .values(domain_id=domain_id)
    )
    return result.rowcount
```

- [ ] **Step 4: Migrate `backend/app/services/ingestion/report_writer.py`**

Add the import:

```python
from app.repositories.dmarc_reports import (
    distinct_header_froms_for_domain_or_descendants,
    insert_aggregate_report_if_new,
    insert_forensic_report_if_new,
    insert_tls_rpt_report_if_new,
    list_unmatched_aggregate_reports,
    list_unmatched_forensic_reports_for_org,
    list_unmatched_tls_rpt_reports_for_org,
    update_record_domain_id_for_header_from,
)
```

In `write_aggregate_report`, replace:

```python
    try:
        async with db.begin_nested():
            db.add(report)
            await db.flush()
    except IntegrityError:
        logger.debug("duplicate aggregate report %s from %s, skipping", metadata.get("report_id"), metadata.get("org_name"))
        return False
```

with:

```python
    if not await insert_aggregate_report_if_new(db, report):
        logger.debug("duplicate aggregate report %s from %s, skipping", metadata.get("report_id"), metadata.get("org_name"))
        return False
```

Same pattern in `write_forensic_report` (using `insert_forensic_report_if_new`) and inside `write_smtp_tls_report`'s loop (using `insert_tls_rpt_report_if_new`, keeping the `written += 1` / loop structure intact — only the try/except body changes to an `if` check).

In `resweep_unmatched_reports`, replace the three inline `select(...)` blocks:

```python
    agg_result = await db.execute(
        select(DmarcAggregateReport).where(
            DmarcAggregateReport.organization_id == organization_id, DmarcAggregateReport.domain_id.is_(None)
        )
    )
    for report in agg_result.scalars().all():
```

with:

```python
    for report in await list_unmatched_aggregate_reports(db, organization_id, limit=None):
```

and the forensic/TLS-RPT blocks similarly with `list_unmatched_forensic_reports_for_org(db, organization_id)` / `list_unmatched_tls_rpt_reports_for_org(db, organization_id)`.

In `resweep_domain_records`, replace:

```python
    candidates = (
        await db.execute(
            select(DmarcAggregateRecord.header_from)
            .where(
                DmarcAggregateRecord.organization_id == organization_id,
                or_(
                    DmarcAggregateRecord.header_from == domain.name,
                    DmarcAggregateRecord.header_from.like(f"%.{domain.name}"),
                ),
            )
            .distinct()
        )
    ).scalars().all()
```

with:

```python
    candidates = await distinct_header_froms_for_domain_or_descendants(db, organization_id, domain.name)
```

and:

```python
        result = await db.execute(
            DmarcAggregateRecord.__table__.update()
            .where(
                DmarcAggregateRecord.organization_id == organization_id,
                DmarcAggregateRecord.header_from == header_from,
                DmarcAggregateRecord.domain_id.is_distinct_from(resolved_domain_id),
            )
            .values(domain_id=resolved_domain_id)
        )
        updated += result.rowcount
```

with:

```python
        updated += await update_record_domain_id_for_header_from(db, organization_id, header_from, resolved_domain_id)
```

Remove `from sqlalchemy import or_, select` and `from sqlalchemy.exc import IntegrityError` from this file once nothing else needs them (check first — `Domain` import stays, it's still used as a type hint in `resweep_domain_records`'s signature).

- [ ] **Step 5: Write repository tests**

```python
# backend/tests/repositories/test_dmarc_reports_ingestion.py
import uuid
from datetime import datetime, timezone

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.domain import Domain
from app.repositories.dmarc_reports import (
    distinct_header_froms_for_domain_or_descendants,
    insert_aggregate_report_if_new,
    insert_forensic_report_if_new,
    list_unmatched_aggregate_reports,
    list_unmatched_forensic_reports_for_org,
    update_record_domain_id_for_header_from,
)

from tests.conftest import seed_org_and_user


def _agg_report(org_id, *, domain_id=None, report_id=None, org_name="reporter.example") -> DmarcAggregateReport:
    now = datetime.now(timezone.utc)
    return DmarcAggregateReport(
        organization_id=org_id, domain_id=domain_id, report_id=report_id or str(uuid.uuid4()),
        org_name=org_name, date_range_begin=now, date_range_end=now,
        policy_published_domain="example.com", received_at=now,
    )


async def test_insert_aggregate_report_if_new_rejects_duplicate(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    natural_key_report_id = "dup-test-1"

    async with owner_factory() as db:
        first = await insert_aggregate_report_if_new(db, _agg_report(org.id, report_id=natural_key_report_id))
        await db.commit()
    assert first is True

    async with owner_factory() as db:
        second = await insert_aggregate_report_if_new(db, _agg_report(org.id, report_id=natural_key_report_id))
        await db.commit()
    assert second is False


async def test_insert_forensic_report_if_new_rejects_duplicate(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)

    def _forensic(source_message_id):
        return DmarcForensicReport(
            organization_id=org.id, arrival_date=now, reported_domain="example.com",
            source_message_id=source_message_id, created_at=now,
        )

    async with owner_factory() as db:
        first = await insert_forensic_report_if_new(db, _forensic("msg-1"))
        await db.commit()
    assert first is True

    async with owner_factory() as db:
        second = await insert_forensic_report_if_new(db, _forensic("msg-1"))
        await db.commit()
    assert second is False


async def test_list_unmatched_aggregate_reports_no_limit_returns_all(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)

    async with owner_factory() as db:
        for i in range(3):
            await insert_aggregate_report_if_new(db, _agg_report(org.id, report_id=f"unmatched-{i}"))
        await db.commit()

    async with owner_factory() as db:
        results = await list_unmatched_aggregate_reports(db, org.id, limit=None)
    assert len(results) == 3


async def test_list_unmatched_forensic_reports_for_org(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        db.add(DmarcForensicReport(
            organization_id=org.id, domain_id=None, arrival_date=now,
            reported_domain="example.com", source_message_id="fx-1", created_at=now,
        ))
        await db.commit()

    async with owner_factory() as db:
        results = await list_unmatched_forensic_reports_for_org(db, org.id)
    assert len(results) == 1


async def test_distinct_header_froms_and_update_domain_id(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        report = _agg_report(org.id, report_id="hf-test")
        db.add(report)
        await db.flush()
        db.add(DmarcAggregateRecord(
            organization_id=org.id, report_id=report.id, domain_id=None, source_ip="203.0.113.5",
            count=1, disposition="none", dkim_result="pass", spf_result="pass",
            header_from="mail.example.com", auth_results={}, created_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    async with owner_factory() as db:
        candidates = await distinct_header_froms_for_domain_or_descendants(db, org.id, "example.com")
    assert "mail.example.com" in candidates

    async with owner_factory() as db:
        updated = await update_record_domain_id_for_header_from(db, org.id, "mail.example.com", domain.id)
        await db.commit()
    assert updated == 1
```

Note: `Disposition`/`AuthResult` enum values used as bare strings (`"none"`, `"pass"`) above may need to be the actual enum members depending on how SQLAlchemy's `pg_enum` type coercion behaves in this codebase — check an existing test file (e.g. `tests/repositories/test_dmarc_reports_analytics.py` from the previous plan) for the established pattern and match it exactly rather than guessing.

- [ ] **Step 6: Run tests**

Run: `pytest tests/repositories/test_dmarc_reports_ingestion.py tests/routers/test_domains.py -v`, then the full suite (this touches core ingestion — run everything).
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/services/ingestion/report_writer.py backend/tests/repositories/test_dmarc_reports_ingestion.py
git commit -m "Move report_writer.py's queries onto the repository layer"
```

---

### Task 5: `forensic_purge.py`

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add 1 function)
- Modify: `backend/app/services/retention/forensic_purge.py`
- Modify: `backend/tests/repositories/test_dmarc_reports_ingestion.py` (add 1 test) or a new file if you judge that cleaner — your call, but don't create a third `test_dmarc_reports_*` file without a clear reason, this plan already has two.

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.repositories.dmarc_reports.purge_forensic_raw_messages_older_than(db, cutoff) -> int` — consumed only by this task.

- [ ] **Step 1: Add the function**

```python
async def purge_forensic_raw_messages_older_than(db: AsyncSession, cutoff: datetime) -> int:
    """Nulls out raw_message (and only that column — see the caller's own
    docstring for why authentication_results is left alone) for rows older
    than `cutoff`. Returns the number of rows affected."""
    result = await db.execute(
        DmarcForensicReport.__table__.update()
        .where(DmarcForensicReport.raw_message.is_not(None), DmarcForensicReport.created_at < cutoff)
        .values(raw_message=None)
    )
    return result.rowcount
```

- [ ] **Step 2: Migrate `purge_old_forensic_raw_messages`**

```python
from app.repositories.dmarc_reports import purge_forensic_raw_messages_older_than
```

```python
async def purge_old_forensic_raw_messages(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    return await purge_forensic_raw_messages_older_than(db, cutoff)
```

Remove now-unused imports (`AsyncSession` stays, still a type hint; check if anything else needs removing — this file is tiny).

- [ ] **Step 3: Write a test and run it**

```python
async def test_purge_forensic_raw_messages_older_than(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=45)

    async with owner_factory() as db:
        db.add(DmarcForensicReport(
            organization_id=org.id, arrival_date=old, reported_domain="example.com",
            source_message_id="old-1", raw_message="secret contents", created_at=old,
        ))
        db.add(DmarcForensicReport(
            organization_id=org.id, arrival_date=now, reported_domain="example.com",
            source_message_id="new-1", raw_message="still fresh", created_at=now,
        ))
        await db.commit()

    async with owner_factory() as db:
        purged = await purge_forensic_raw_messages_older_than(db, now - timedelta(days=30))
        await db.commit()
    assert purged == 1
```

Add `timedelta` to the test file's imports if not already present. Run: `pytest tests/repositories/test_dmarc_reports_ingestion.py -v`, then the full suite.
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/services/retention/forensic_purge.py backend/tests/repositories/test_dmarc_reports_ingestion.py
git commit -m "Move forensic_purge.py's query onto the repository layer"
```

---

### Task 6: `workers/scheduler.py` + `hosted_reports_poll_job.py`

**Files:**
- Modify: `backend/app/repositories/mailbox_connections.py` (add 2 functions)
- Modify: `backend/app/workers/scheduler.py`
- Modify: `backend/app/workers/jobs/hosted_reports_poll_job.py`
- Create: `backend/tests/repositories/test_mailbox_connections.py` (create if it doesn't exist; check first)

**Interfaces:**
- Consumes: `app.repositories.domains.list_domains_with_hosted_report_address` (Task 2).
- Produces: `app.repositories.mailbox_connections.list_orgs_with_granted_mailbox_connections(db) -> Sequence[tuple]`, `.get_or_create_hosted_reports_poll_state(db) -> HostedReportsPollState` — consumed only within this task.

- [ ] **Step 1: Add the two functions to `backend/app/repositories/mailbox_connections.py`**

New imports needed: `from app.models.enums import ConsentStatus` (if not present), `from app.models.organization import Organization`, `from app.models.hosted_reports_poll_state import HostedReportsPollState`.

```python
async def list_orgs_with_granted_mailbox_connections(db: AsyncSession) -> Sequence:
    """(organization_id, entra_tenant_id) for every org with a granted
    mailbox connection — the worker's own job-registration list, cross-org
    by design (see app/workers/scheduler.py's _list_pollable_orgs)."""
    result = await db.execute(
        select(Organization.id, Organization.entra_tenant_id)
        .join(MailboxConnection, MailboxConnection.organization_id == Organization.id)
        .where(
            MailboxConnection.consent_status == ConsentStatus.granted,
            Organization.entra_tenant_id.is_not(None),
        )
    )
    return result.all()


async def get_or_create_hosted_reports_poll_state(db: AsyncSession) -> HostedReportsPollState:
    result = await db.execute(select(HostedReportsPollState).limit(1))
    state = result.scalar_one_or_none()
    if state is None:
        state = HostedReportsPollState()
        db.add(state)
        await db.flush()
    return state
```

- [ ] **Step 2: Migrate `backend/app/workers/scheduler.py`**

Add `from app.repositories.mailbox_connections import list_orgs_with_granted_mailbox_connections`.

```python
async def _list_pollable_orgs() -> list:
    """Cross-org by design (the worker isn't acting on behalf of any one
    tenant) — uses the is_platform_admin RLS bypass rather than a per-org
    context, same mechanism the platform-admin API routes use."""
    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        return list(await list_orgs_with_granted_mailbox_connections(db))
```

Remove `from sqlalchemy import select` and the `Organization`/`MailboxConnection`/`ConsentStatus` imports from this file if nothing else in it uses them (check — the rest of the file is job-scheduling wiring, no other model references expected, but verify).

- [ ] **Step 3: Migrate `backend/app/workers/jobs/hosted_reports_poll_job.py`**

Add `from app.repositories.mailbox_connections import get_or_create_hosted_reports_poll_state` and `from app.repositories.domains import list_domains_with_hosted_report_address`.

Replace `_get_or_create_state` entirely — delete the function, and change its two call sites (`state = await _get_or_create_state(db)`, appearing twice in this file) to `state = await get_or_create_hosted_reports_poll_state(db)`.

Replace:

```python
            address_map = {
                d.hosted_report_address.lower(): (d.id, d.organization_id)
                for d in (
                    await db.execute(select(Domain).where(Domain.hosted_report_address.is_not(None)))
                ).scalars()
            }
```

with:

```python
            address_map = {
                d.hosted_report_address.lower(): (d.id, d.organization_id)
                for d in await list_domains_with_hosted_report_address(db)
            }
```

Remove `from sqlalchemy import select` (check `text` is still needed — yes, for the advisory lock) and the `Domain`/`HostedReportsPollState` imports if nothing else in the file uses them (check — `HostedReportsPollState` was only used as `_get_or_create_state`'s return type annotation, now gone; `Domain` was only used in the removed query).

- [ ] **Step 4: Write repository tests**

```python
# backend/tests/repositories/test_mailbox_connections.py (add if the file exists already; check first)
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.repositories.mailbox_connections import (
    get_or_create_hosted_reports_poll_state,
    list_orgs_with_granted_mailbox_connections,
)

from tests.conftest import seed_org_and_user


async def test_list_orgs_with_granted_mailbox_connections(api):
    _client, owner_factory = api
    import uuid
    org, _user = await seed_org_and_user(owner_factory, entra=True)

    async with owner_factory() as db:
        db.add(MailboxConnection(
            organization_id=org.id, consent_status=ConsentStatus.granted, mailbox_address="reports@example.com",
        ))
        await db.commit()

    async with owner_factory() as db:
        results = await list_orgs_with_granted_mailbox_connections(db)
    org_ids = {r[0] for r in results}
    assert org.id in org_ids


async def test_get_or_create_hosted_reports_poll_state_creates_once(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        first = await get_or_create_hosted_reports_poll_state(db)
        await db.commit()
        first_id = first.id

    async with owner_factory() as db:
        second = await get_or_create_hosted_reports_poll_state(db)
    assert second.id == first_id
```

Check whether `seed_org_and_user(owner_factory, entra=True)` is the right way to get an org with a non-null `entra_tenant_id` — look at how other tests in this codebase seed an org that needs `entra_tenant_id` set, and match that pattern exactly rather than guessing at the helper's exact kwarg.

- [ ] **Step 5: Run tests**

Run: `pytest tests/repositories/test_mailbox_connections.py -v`, then the full suite.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/mailbox_connections.py backend/app/workers/scheduler.py backend/app/workers/jobs/hosted_reports_poll_job.py backend/tests/repositories/test_mailbox_connections.py
git commit -m "Move scheduler.py and hosted_reports_poll_job.py's queries onto the repository layer"
```

---

### Task 7: `mailbox_poll_job.py`

**Files:**
- Modify: `backend/app/workers/jobs/mailbox_poll_job.py`

**Interfaces:**
- Consumes: `app.repositories.mailbox_connections.get_org_mailbox_connection` (pre-existing, from an earlier plan).
- Produces: nothing new.

- [ ] **Step 1: Identify the duplicate**

Both occurrences of `select(MailboxConnection).where(MailboxConnection.organization_id == organization_id)` in this file (in `_do_poll`'s success path and its exception-handler path) are byte-identical to the pre-existing `get_org_mailbox_connection(db, organization_id) -> MailboxConnection | None` in `backend/app/repositories/mailbox_connections.py`.

- [ ] **Step 2: Migrate both call sites**

Add `from app.repositories.mailbox_connections import get_org_mailbox_connection`.

Replace both:

```python
            result = await db.execute(
                select(MailboxConnection).where(MailboxConnection.organization_id == organization_id)
            )
            connection = result.scalar_one_or_none()
```

occurrences with:

```python
            connection = await get_org_mailbox_connection(db, organization_id)
```

Remove `from sqlalchemy import select, text` → keep `text` (used for the advisory lock), drop `select`. Remove the `MailboxConnection` model import only if nothing else in this file still references the class name directly (check — it's used as a type hint nowhere in this file based on the current version, but verify before removing).

- [ ] **Step 3: Run tests**

This file has no direct unit test (it's exercised via the `api` fixture's `mailbox_poll_job_module` patching, per `tests/conftest.py`'s own docstring, when other tests' BackgroundTasks reach it — check if there's a dedicated test file for it first). Run the full suite — this is a pure internal refactor of two identical call sites, and the full suite's mailbox-connection-touching tests are the safety net here.
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/workers/jobs/mailbox_poll_job.py
git commit -m "Move mailbox_poll_job.py's queries onto the repository layer, reusing get_org_mailbox_connection"
```

---

### Task 8: `session_manager.py` (security-sensitive)

**Files:**
- Modify: `backend/app/repositories/auth.py` (add 2 functions)
- Modify: `backend/app/services/auth/session_manager.py`
- Create: `backend/tests/repositories/test_auth.py` (create if it doesn't exist; check first)

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.repositories.auth.get_user_session_by_token_hash(db, token_hash) -> UserSession | None`, `.get_platform_admin_session_by_token_hash(db, token_hash) -> PlatformAdminSession | None` — consumed only by this task.

**Security note — read before starting:** `session_manager.py`'s `get_active_user_session`/`get_active_platform_admin_session` hash the raw token BEFORE querying (never query by raw token), then call `_validate_and_refresh` on whatever row comes back (checking `revoked_at`, expiry, and refreshing `last_seen_at`). Only the SELECT-by-hash itself moves to the repository — the hashing, the `_validate_and_refresh` call, and all fail-closed logic stay exactly where they are, unchanged, in `session_manager.py`. Do not touch anything about how tokens are hashed or compared.

- [ ] **Step 1: Read `backend/app/repositories/auth.py` in full first**

It already has several `get_*_by_token_hash`-shaped functions (`get_mfa_pending_challenge`, `get_unused_recovery_code`, `get_password_setup_token`) — match their exact style.

- [ ] **Step 2: Add the two functions**

New imports needed: `from app.models.session import UserSession`, `from app.models.platform_admin_session import PlatformAdminSession` (check exact model file paths first — read `session_manager.py`'s own imports to confirm).

```python
async def get_user_session_by_token_hash(db: AsyncSession, token_hash: str) -> UserSession | None:
    result = await db.execute(select(UserSession).where(UserSession.session_token_hash == token_hash))
    return result.scalar_one_or_none()


async def get_platform_admin_session_by_token_hash(db: AsyncSession, token_hash: str) -> PlatformAdminSession | None:
    result = await db.execute(select(PlatformAdminSession).where(PlatformAdminSession.session_token_hash == token_hash))
    return result.scalar_one_or_none()
```

- [ ] **Step 3: Migrate `backend/app/services/auth/session_manager.py`**

Add `from app.repositories.auth import get_platform_admin_session_by_token_hash, get_user_session_by_token_hash`.

In `get_active_user_session`, replace:

```python
    result = await db.execute(select(UserSession).where(UserSession.session_token_hash == token_hash))
    session = result.scalar_one_or_none()
```

with:

```python
    session = await get_user_session_by_token_hash(db, token_hash)
```

Same pattern in `get_active_platform_admin_session` with `get_platform_admin_session_by_token_hash`. The `_hash_token(raw_token)` call immediately before, and the `_validate_and_refresh(session)` call immediately after, in both functions, stay completely unchanged.

Remove `from sqlalchemy import select` if nothing else in this file needs it (check — `create_user_session`/`create_platform_admin_session` use `db.add()`, not `select()`, so this import likely becomes fully unused; verify).

- [ ] **Step 4: Write repository tests**

```python
# backend/tests/repositories/test_auth.py (add if the file exists already; check first)
import hashlib

from app.repositories.auth import get_platform_admin_session_by_token_hash, get_user_session_by_token_hash
from app.services.auth import session_manager

from tests.conftest import login_as_platform_admin, seed_org_and_user


async def test_get_user_session_by_token_hash_round_trips(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)

    async with owner_factory() as db:
        _session, raw_token = await session_manager.create_user_session(
            db, user_id=user.id, organization_id=org.id, ip_address="127.0.0.1", user_agent="pytest",
        )
        await db.commit()

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    async with owner_factory() as db:
        found = await get_user_session_by_token_hash(db, token_hash)
    assert found is not None
    assert found.user_id == user.id


async def test_get_user_session_by_token_hash_returns_none_for_unknown_hash(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        result = await get_user_session_by_token_hash(db, "a" * 64)
    assert result is None


async def test_get_platform_admin_session_by_token_hash_round_trips(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    raw_token = client.cookies.get("dmarc_admin_session")
    assert raw_token is not None

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    async with owner_factory() as db:
        found = await get_platform_admin_session_by_token_hash(db, token_hash)
    assert found is not None
```

Check the exact platform-admin session cookie name via `settings.platform_admin_session_cookie_name` rather than hardcoding `"dmarc_admin_session"` if you're not certain it's still that value — grep `app/config.py` to confirm before writing this test.

- [ ] **Step 5: Run tests**

Run: `pytest tests/repositories/test_auth.py tests/routers/test_auth.py tests/routers/test_platform_admin.py -v`, then the full suite. This is a security-sensitive area — do not skip the full suite here.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/auth.py backend/app/services/auth/session_manager.py backend/tests/repositories/test_auth.py
git commit -m "Move session_manager.py's queries onto the repository layer"
```

---

### Task 9: `inbound_view.py` + `scheduled_recheck.py`

**Files:**
- Modify: `backend/app/repositories/dns_checks.py` (add 1 function)
- Modify: `backend/app/services/dns_checks/inbound_view.py`
- Modify: `backend/app/services/dns_checks/scheduled_recheck.py`
- Modify: `backend/tests/repositories/test_dns_checks.py`

**Interfaces:**
- Consumes: `app.repositories.dns_checks.list_latest_check_results` (pre-existing), `app.repositories.selectors.list_selectors_for_domain` (pre-existing), `app.repositories.mailbox_connections.get_org_mailbox_connection` (pre-existing).
- Produces: `app.repositories.dns_checks.list_domains_due_for_check(db, cutoff) -> Sequence[UUID]` — consumed only by this task.

- [ ] **Step 1: Migrate `inbound_view.py` — pure duplicate elimination, zero new repository code**

`build_inbound_hosts`'s query is identical in shape and result to the pre-existing `list_latest_check_results(db, domain_id) -> Sequence[DnsCheckResult]`. Add `from app.repositories.dns_checks import list_latest_check_results`, and replace:

```python
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at)).where(DnsCheckResult.domain_id == domain_id).scalar_subquery()
    )
    result = await db.execute(
        select(DnsCheckResult).where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.checked_at == latest_ts)
    )
    rows = result.scalars().all()
```

with:

```python
    rows = await list_latest_check_results(db, domain_id)
```

Remove `from sqlalchemy import func, select` (check nothing else in the file uses them — it shouldn't). `DnsCheckResult` model import stays (still used for `row.check_type`/`CheckType` comparisons in the loop below).

- [ ] **Step 2: Add `list_domains_due_for_check` to `backend/app/repositories/dns_checks.py`**

```python
async def list_domains_due_for_check(db: AsyncSession, cutoff: datetime) -> Sequence[UUID]:
    """Verified, active domains whose latest DnsCheckResult is older than
    `cutoff`, or that have never been checked at all. See
    app/services/dns_checks/scheduled_recheck.py's run_dns_check_sweep."""
    latest_checked = (
        select(DnsCheckResult.domain_id, func.max(DnsCheckResult.checked_at).label("latest"))
        .group_by(DnsCheckResult.domain_id)
        .subquery()
    )
    result = await db.execute(
        select(Domain.id)
        .outerjoin(latest_checked, latest_checked.c.domain_id == Domain.id)
        .where(
            Domain.verification_status == DomainVerificationStatus.verified,
            Domain.is_active.is_(True),
            (latest_checked.c.latest.is_(None)) | (latest_checked.c.latest < cutoff),
        )
    )
    return list(result.scalars().all())
```

New imports needed: `from datetime import datetime`, `from app.models.domain import Domain`, `from app.models.enums import CheckType, DomainVerificationStatus` (check `CheckType` isn't already imported before adding it again).

- [ ] **Step 3: Migrate `scheduled_recheck.py`'s three queries**

Add `from app.repositories.dns_checks import list_domains_due_for_check`, `from app.repositories.selectors import list_selectors_for_domain`, `from app.repositories.mailbox_connections import get_org_mailbox_connection`.

Replace:

```python
    selector_result = await db.execute(select(DkimSelector).where(DkimSelector.domain_id == domain.id))
    selectors = selector_result.scalars().all()
```

with:

```python
    selectors = await list_selectors_for_domain(db, domain.id)
```

(Note: `list_selectors_for_domain` orders its result by `DkimSelector.selector`; the original inline query had no explicit order. Confirm this doesn't change behavior — the caller only builds a `dict` keyed by selector name from this list, order-independent, so it's safe. State this explicitly in your report.)

Replace:

```python
    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == domain.organization_id))
    ).scalar_one_or_none()
```

with:

```python
    connection = await get_org_mailbox_connection(db, domain.organization_id)
```

Replace `_due_domain_ids` entirely:

```python
async def _due_domain_ids(db: AsyncSession, cutoff: datetime) -> list[uuid.UUID]:
    return list(await list_domains_due_for_check(db, cutoff))
```

(Keep this thin wrapper rather than inlining the call at its one call site in `run_dns_check_sweep` — matches this plan's established pattern of preserving existing function names/call structure everywhere else.)

Remove `from sqlalchemy import func, select` if nothing else in this file uses them (check — `db.get(Domain, ...)` and `db.get(Organization, ...)` calls elsewhere in this file use `db.get()`, not `select()`, and are correctly left untouched per this plan's stated `db.get()` exclusion). Remove `DkimSelector`/`MailboxConnection` model imports if nothing else references them directly (check first).

- [ ] **Step 4: Write/extend repository tests**

Add to `backend/tests/repositories/test_dns_checks.py` (from the previous plan):

```python
async def test_list_domains_due_for_check_includes_never_checked_and_stale(api):
    from datetime import datetime, timedelta, timezone

    from app.models.domain import Domain
    from app.models.enums import DomainVerificationStatus
    from app.repositories.dns_checks import list_domains_due_for_check

    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        never_checked = Domain(
            organization_id=org.id, name="never.example",
            verification_status=DomainVerificationStatus.verified, is_active=True,
        )
        not_due = Domain(
            organization_id=org.id, name="unverified.example",
            verification_status=DomainVerificationStatus.pending, is_active=True,
        )
        db.add_all([never_checked, not_due])
        await db.commit()
        await db.refresh(never_checked)

    async with owner_factory() as db:
        due = await list_domains_due_for_check(db, datetime.now(timezone.utc) - timedelta(hours=6))
    assert never_checked.id in due
    assert not_due.id not in due  # not verified, never eligible regardless of check history
```

Check `seed_org_and_user` is already imported in this test file (it likely is from the previous plan) before adding a duplicate import.

- [ ] **Step 5: Run tests**

Run: `pytest tests/repositories/test_dns_checks.py tests/routers/test_dns_checks.py -v`, then the full suite.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dns_checks.py backend/app/services/dns_checks/inbound_view.py backend/app/services/dns_checks/scheduled_recheck.py backend/tests/repositories/test_dns_checks.py
git commit -m "Move inbound_view.py and scheduled_recheck.py's queries onto the repository layer, reusing two existing repository functions"
```

---

### Task 10: `update_check.py` (new repository file)

**Files:**
- Create: `backend/app/repositories/admin_updates.py`
- Modify: `backend/app/services/update_check.py`
- Create: `backend/tests/repositories/test_admin_updates.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.repositories.admin_updates.get_or_create_update_check_state(db) -> UpdateCheckState` — consumed only by this task.

- [ ] **Step 1: Create the repository file**

```python
# backend/app/repositories/admin_updates.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.update_check_state import UpdateCheckState


async def get_or_create_update_check_state(db: AsyncSession) -> UpdateCheckState:
    result = await db.execute(select(UpdateCheckState).limit(1))
    state = result.scalar_one_or_none()
    if state is None:
        state = UpdateCheckState()
        db.add(state)
        await db.flush()
    return state
```

- [ ] **Step 2: Migrate `backend/app/services/update_check.py`**

Add `from app.repositories.admin_updates import get_or_create_update_check_state`.

Delete the existing `get_or_create_state` function body and replace its definition with:

```python
async def get_or_create_state(db) -> UpdateCheckState:
    """Also used directly by GET /admin/updates to read the cached result
    without re-running the check."""
    return await get_or_create_update_check_state(db)
```

Check whether `backend/app/routers/admin_updates.py` imports `get_or_create_state` from `update_check` directly (it does, per this router's existing code) — confirm that import and call site are unaffected by keeping this thin wrapper in place under its original name.

Remove `from sqlalchemy import select` from `update_check.py` if nothing else in the file needs it (the rest of `run_update_check` uses `httpx`, not SQLAlchemy, for the actual GitHub API check — verify before removing).

- [ ] **Step 3: Write repository tests**

```python
# backend/tests/repositories/test_admin_updates.py
from app.repositories.admin_updates import get_or_create_update_check_state


async def test_get_or_create_update_check_state_creates_once(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        first = await get_or_create_update_check_state(db)
        await db.commit()
        first_id = first.id

    async with owner_factory() as db:
        second = await get_or_create_update_check_state(db)
    assert second.id == first_id
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/repositories/test_admin_updates.py tests/routers/test_admin_updates.py -v`, then the full suite.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/admin_updates.py backend/app/services/update_check.py backend/tests/repositories/test_admin_updates.py
git commit -m "Move update_check.py's query onto a new repository file"
```

---

### Task 11: `seed_demo.py` + `seed_demo_reports.py`

**Files:**
- Modify: `backend/app/repositories/sign_in_events.py` (add 1 function)
- Modify: `backend/app/repositories/users.py` (add 1 function)
- Modify: `backend/app/scripts/seed_demo.py`
- Modify: `backend/app/scripts/seed_demo_reports.py`

**Interfaces:**
- Consumes: `app.repositories.organizations.get_org_by_demo_flag` (Task 1), `app.repositories.domains.get_domain_by_org_and_name` (Task 2), `app.repositories.selectors.known_selector_names` (pre-existing), `app.repositories.source_identification.upsert_resolved_identities` (pre-existing), `app.repositories.dmarc_reports.insert_aggregate_report_if_new` (Task 4).
- Produces: `app.repositories.sign_in_events.count_sign_in_events_for_org(db, organization_id) -> int`, `app.repositories.users.get_user_by_org_and_email(db, organization_id, email) -> User | None` — consumed only within this task.

- [ ] **Step 1: Add `count_sign_in_events_for_org` to `backend/app/repositories/sign_in_events.py`**

```python
async def count_sign_in_events_for_org(db: AsyncSession, organization_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(SignInEvent).where(SignInEvent.organization_id == organization_id)
    )
    return result.scalar_one()
```

Add `from sqlalchemy import func` (extend the existing `from sqlalchemy import select, tuple_` line).

- [ ] **Step 2: Add `get_user_by_org_and_email` to `backend/app/repositories/users.py`**

```python
async def get_user_by_org_and_email(db: AsyncSession, organization_id: UUID, email: str) -> User | None:
    result = await db.execute(select(User).where(User.organization_id == organization_id, User.email == email))
    return result.scalar_one_or_none()
```

Check this file's existing imports (`select`, `User`, `UUID` likely already present given `get_user_in_org` exists) before adding duplicates.

- [ ] **Step 3: Migrate `backend/app/scripts/seed_demo.py`**

Add imports:

```python
from app.repositories.domains import get_domain_by_org_and_name
from app.repositories.organizations import get_org_by_demo_flag
from app.repositories.selectors import known_selector_names
from app.repositories.sign_in_events import count_sign_in_events_for_org
from app.repositories.users import get_user_by_org_and_email
```

Replace:

```python
        result = await db.execute(select(Organization).where(Organization.is_demo_read_only.is_(True)))
        org = result.scalar_one_or_none()
```

with:

```python
        org = await get_org_by_demo_flag(db)
```

Replace both occurrences of:

```python
            domain = (
                await db.execute(select(Domain).where(Domain.organization_id == org.id, Domain.name == DEMO_DOMAIN))
            ).scalar_one_or_none()
```

with:

```python
            domain = await get_domain_by_org_and_name(db, org.id, DEMO_DOMAIN)
```

Replace:

```python
            existing_selectors = set(
                (
                    await db.execute(select(DkimSelector.selector).where(DkimSelector.domain_id == domain.id))
                ).scalars()
            )
```

with:

```python
            existing_selectors = await known_selector_names(db, domain.id)
```

Replace:

```python
        existing_events = (
            await db.execute(select(func.count()).select_from(SignInEvent).where(SignInEvent.organization_id == org.id))
        ).scalar_one()
```

with:

```python
        existing_events = await count_sign_in_events_for_org(db, org.id)
```

Replace:

```python
            demo_user = (
                await db.execute(select(User).where(User.organization_id == org.id, User.email == settings.demo_login_email))
            ).scalar_one()
```

with:

```python
            demo_user = await get_user_by_org_and_email(db, org.id, settings.demo_login_email)
```

(Note: the original used `.scalar_one()`, which raises if not found — `get_user_by_org_and_email` returns `None` on no match via `scalar_one_or_none()`, matching this plan's established repository convention. This branch is only reached when `existing_events == 0` right after `org`/`domain` were just resolved and the demo user was created earlier in this same script run or a prior one, so a `None` here would indicate a genuine bug elsewhere, not an expected outcome — preserve the original crash-on-missing behavior by keeping an explicit assertion in the script: `assert demo_user is not None` right after the call, rather than silently letting the next line's `demo_user.id` throw a less obvious `AttributeError`.)

Remove `from sqlalchemy import select, func` (check both are otherwise unused in this file — they should be, verify) and the `Organization`/`Domain`/`DkimSelector`/`SignInEvent`/`User` model imports for any that become unused (check each individually — `User(...)` is still constructed earlier in `main()`, so that import stays; `DkimSelector(...)` is still constructed too; verify the others).

- [ ] **Step 4: Migrate `backend/app/scripts/seed_demo_reports.py`**

Add imports:

```python
from app.repositories.dmarc_reports import insert_aggregate_report_if_new
from app.repositories.domains import get_domain_by_org_and_name
from app.repositories.organizations import get_org_by_demo_flag
from app.repositories.source_identification import upsert_resolved_identities
```

Replace:

```python
        org = (
            await db.execute(select(Organization).where(Organization.is_demo_read_only.is_(True)))
        ).scalar_one_or_none()
```

with:

```python
        org = await get_org_by_demo_flag(db)
```

Replace:

```python
        domain = (
            await db.execute(select(Domain).where(Domain.organization_id == org.id, Domain.name == DEMO_DOMAIN))
        ).scalar_one_or_none()
```

with:

```python
        domain = await get_domain_by_org_and_name(db, org.id, DEMO_DOMAIN)
```

Replace:

```python
        stmt = pg_insert(SourceIpIdentity).values(identity_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceIpIdentity.source_ip],
            set_={"ptr_hostname": stmt.excluded.ptr_hostname, "service_label": stmt.excluded.service_label, "match_method": stmt.excluded.match_method, "fcrdns_valid": stmt.excluded.fcrdns_valid, "resolved_at": stmt.excluded.resolved_at},
        )
        await db.execute(stmt)
```

with:

```python
        await upsert_resolved_identities(db, identity_rows)
```

(`identity_rows` is already built as a `list[dict]` with exactly the keys `upsert_resolved_identities` expects — `source_ip`, `ptr_hostname`, `service_label`, `match_method`, `fcrdns_valid`, `resolved_at` — confirmed by reading the dict-construction code immediately above this block. Verify this yourself before making the change; if the keys don't match exactly, this is a real problem to flag, not paper over.)

Replace:

```python
                already = (
                    await db.execute(
                        select(DmarcAggregateReport.id).where(
                            DmarcAggregateReport.organization_id == org.id,
                            DmarcAggregateReport.org_name == reporter,
                            DmarcAggregateReport.report_id == report_id,
                            DmarcAggregateReport.policy_published_domain == DEMO_DOMAIN,
                        )
                    )
                ).scalar_one_or_none()
                if already is not None:
                    continue

                report = DmarcAggregateReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    report_id=report_id,
                    org_name=reporter,
                    email=None,
                    date_range_begin=date,
                    date_range_end=date + timedelta(days=1),
                    policy_published_domain=DEMO_DOMAIN,
                    policy_p=policy_p,
                    policy_sp=policy_p,
                    policy_pct=100,
                    policy_adkim="r",
                    policy_aspf="r",
                    source_message_id=f"demo-seed:{reporter}:{date:%Y%m%d}",
                    received_at=date + timedelta(days=1, hours=6),
                )
                db.add(report)
                await db.flush()
```

with:

```python
                report = DmarcAggregateReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    report_id=report_id,
                    org_name=reporter,
                    email=None,
                    date_range_begin=date,
                    date_range_end=date + timedelta(days=1),
                    policy_published_domain=DEMO_DOMAIN,
                    policy_p=policy_p,
                    policy_sp=policy_p,
                    policy_pct=100,
                    policy_adkim="r",
                    policy_aspf="r",
                    source_message_id=f"demo-seed:{reporter}:{date:%Y%m%d}",
                    received_at=date + timedelta(days=1, hours=6),
                )
                if not await insert_aggregate_report_if_new(db, report):
                    continue
```

**Read this carefully before making this last change:** the original code checked for an existing report BEFORE constructing/adding the new one (an explicit existence check), while `insert_aggregate_report_if_new` uses `db.begin_nested()` + catching `IntegrityError` on the natural-key unique constraint — a different mechanism reaching the same idempotency outcome. Confirm `DmarcAggregateReport`'s natural key (organization + org_name + report_id + policy_published_domain, per `uq_dmarc_aggregate_reports_natural_key` — check the model file to confirm the exact constraint name/columns) covers the same fields this script's manual pre-check compared, so switching mechanisms doesn't change which rows get treated as duplicates. This script also does `db.add_all(records)` for the report's child records right after — with the old code, that only ran when `already is None`; with the new code, it must only run when `insert_aggregate_report_if_new` returns `True` (the `continue` above handles this identically, skipping the records block below when the report was a duplicate — verify the actual code structure below this point still flows correctly into the `records = []` / `db.add_all(records)` block only on the non-`continue` path).

Remove `from sqlalchemy import select` and `from sqlalchemy.dialects.postgresql import insert as pg_insert` if nothing else in the file uses them (check first). Remove `Organization`/`Domain`/`SourceIpIdentity` model imports if unused (check — `DmarcAggregateReport`/`DmarcAggregateRecord` stay, still constructed directly).

- [ ] **Step 5: Run tests**

These scripts have no existing automated test suite (they're one-off `python -m app.scripts.X` operational tools, not covered by `tests/`) — there is nothing to add here beyond the repository-level tests for the two new small functions (Step 6). Manually verify via a smoke run if you have a way to do so safely against a throwaway database; if not, rely on the repository-level tests plus careful code review of the diff, and say explicitly in your report which verification method you used.

- [ ] **Step 6: Write repository tests for the two new functions**

```python
# add to backend/tests/repositories/test_dmarc_reports_ingestion.py or a sign_in_events/users test file — your call on placement, but check for existing test_sign_in_events.py / test_users.py repository test files first and add there if they exist
from app.repositories.sign_in_events import count_sign_in_events_for_org
from app.repositories.users import get_user_by_org_and_email


async def test_count_sign_in_events_for_org(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        before = await count_sign_in_events_for_org(db, org.id)
    assert before == 0


async def test_get_user_by_org_and_email_found_and_not_found(api):
    _client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        found = await get_user_by_org_and_email(db, org.id, user.email)
        missing = await get_user_by_org_and_email(db, org.id, "nobody@example.com")
    assert found is not None
    assert found.id == user.id
    assert missing is None
```

- [ ] **Step 7: Run the full suite**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/repositories/sign_in_events.py backend/app/repositories/users.py backend/app/scripts/seed_demo.py backend/app/scripts/seed_demo_reports.py backend/tests/repositories/
git commit -m "Move seed_demo.py and seed_demo_reports.py's queries onto the repository layer, reusing two existing repository functions"
```

---

### Task 12: Final verification pass — the whole backend, not just this plan's files

**Files:**
- Read (no modification expected): `backend/app/repositories/README.md`, `backend/app/services/README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — verification only.

- [ ] **Step 1: Sweep the ENTIRE `backend/app/` tree, not just the files this plan touched**

Run: `grep -rln "db\.execute(\|await db\.execute\|pg_insert(" backend/app/ --include="*.py" | grep -v "^backend/app/repositories/"`

Expected: exactly one file, `backend/app/db/rls.py` — the one deliberate, documented exception this plan's Architecture section names. If anything else appears, it means either this plan missed a file (a real gap — investigate and, if genuine, this is a plan defect to rule on per the subagent-driven-development process, not something to wave through) or a task's migration was incomplete (re-open that task).

- [ ] **Step 2: Confirm no duplicate function names were introduced across any repository file this plan touched**

Run, for every repository file this plan modified or created (`dmarc_reports.py`, `dns_checks.py`, `domains.py`, `organizations.py`, `platform_admin.py`, `mailbox_connections.py`, `auth.py`, `users.py`, `sign_in_events.py`, `selectors.py`, `source_identification.py`, `admin_updates.py`):

```bash
for f in backend/app/repositories/*.py; do
  echo "=== $f ==="
  grep -n "^async def" "$f" | sed -E 's/^[0-9]+:async def ([a-zA-Z_]+).*/\1/' | sort | uniq -c | awk '$1 > 1'
done
```

Expected: no output for any file.

- [ ] **Step 3: Confirm `app/repositories/README.md` and `app/services/README.md` still accurately describe the final state**

This plan added a new repository file (`admin_updates.py`) — read the README and decide whether it's worth naming as an additional example, matching the judgment call made for `source_identification.py` in the previous plan. This plan didn't change any service's subpackage-vs-flat-file status — confirm `services/README.md` still holds.

- [ ] **Step 4: Run the full test suite one final time**

Run: `pytest -v`
Expected: every test passes.

- [ ] **Step 5: Commit if Step 3 required a doc edit**

```bash
git add backend/app/repositories/README.md backend/app/services/README.md
git commit -m "Update repository/service docs after the final repository-extraction pass"
```

Skip this commit if no doc edit was made.

- [ ] **Step 6: Confirm this is genuinely the end of this work**

After Step 1's sweep comes back clean (only `db/rls.py`), the cohesion-refactor spec's repository-layer goal is complete: every router and every service/worker/script file in `backend/app/` either has no database access at all, or reaches it exclusively through `app/repositories/`. State this explicitly in the final report so it's on record, not just implied by a clean grep.
