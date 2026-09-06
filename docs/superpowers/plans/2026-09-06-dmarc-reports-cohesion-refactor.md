# DMARC Reports Router Cohesion Refactor (Plan A3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `backend/app/routers/dmarc_reports.py` (1198 lines, 15 endpoints) onto the same schema/repository layers Plan A and Plan A2 already established for the other 12 routers, with full HTTP-level integration test coverage — completing the backend cohesion refactor's router-by-router sequence.

**Architecture:** Same two-layer split as Plan A/A2: a thin `app/schemas/dmarc_reports.py` for the one request-body model, and `app/repositories/dmarc_reports.py` (already exists — created in Plan A for two other routers' cross-cutting needs, extended here) for every SQL query. One genuinely new piece: the pure, DB-free policy-recommendation engine (`_build_policy_recommendation`/`_recommend_np`/`_build_base_recommendation`) moves into the existing `app/services/rating/` subpackage as `policy_recommendation.py`, alongside `domain_rating.py`/`score.py` which it already depends on — this is business logic, not a query, and the `rating/` package already holds exactly this kind of DB-free pure function (`score.py`'s `compute_rating`/`grade_for_score`). The router itself becomes pure orchestration: resolve the owned domain, call repository/service functions, shape the JSON response.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Postgres/RLS (unchanged), pytest-asyncio + httpx `ASGITransport` HTTP-level integration tests (same pattern as every prior router).

**Spec:** `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`

## Global Constraints

- Every repository function takes `db: AsyncSession` as its first argument, returns data (or raises `HTTPException` for an ownership/not-found check), and holds no module-level mutable state (N stateless API replicas must stay safe — see the scale-out work on `v0.1.4-beta`).
- `app/repositories/dmarc_reports.py` already exists (Plan A) with 5 functions used by `domains.py`/`mailbox_connections.py`/`onboarding.py`/`selectors.py` — **do not rename or change the signature of any existing function in it**; this plan only adds new functions to the same file.
- Ownership checks go through `app.repositories.domains.get_owned_domain(db, domain_id, organization_id)` — the router's own local `_get_owned_domain` duplicate is deleted in Task 1, and no later task may reintroduce a local copy.
- Preserve every existing inline comment when moving code — several document non-obvious business rules (RFC citations, race-condition rationale, prior-incident fixes) that must survive the move verbatim or near-verbatim.
- Test-environment landmine (new to this plan, see Task 2): `identify_many`/`service_breakdown` do real reverse-DNS lookups via `app.services.dns_checks.resolver`, which has never been exercised by any existing test. `settings.dns_resolver_host` defaults to `"resolver"`, a hostname that doesn't exist outside the real Docker network — an unpatched call raises `socket.gaierror` (not a graceful timeout). Every test that reaches `service_breakdown`/`identify_many` must monkeypatch `app.services.source_identification.service_identifier.resolve_ptr` to return `None` (see Task 2's fixture).

---

### Task 1: Schema, `get_owned_domain` migration, and the summary/rating/sources cluster

**Files:**
- Create: `backend/app/schemas/dmarc_reports.py`
- Modify: `backend/app/repositories/dmarc_reports.py` (add functions, keep all 5 existing ones untouched)
- Modify: `backend/app/routers/dmarc_reports.py` (delete local `_get_owned_domain`; migrate `dmarc_summary`, `domain_rating`, `dmarc_sources`)
- Create: `backend/tests/routers/test_dmarc_reports.py`

**Interfaces:**
- Consumes: `app.repositories.domains.get_owned_domain(db, domain_id, organization_id) -> Domain` (existing, raises 404 `HTTPException` if not owned).
- Produces: `app.schemas.dmarc_reports.SenderReviewUpdateRequest` (moved as-is, used by Task 2). `app.repositories.dmarc_reports.dmarc_summary_totals(db, domain_id) -> tuple[int, int]`, `.dmarc_disposition_breakdown(db, domain_id) -> dict[str, int]`, `.latest_published_policy_for_domain(db, domain_id) -> str | None` — all consumed only within this task.

- [ ] **Step 1: Create the schema file**

```python
# backend/app/schemas/dmarc_reports.py
from pydantic import BaseModel

from app.models.enums import SenderReviewStatus


class SenderReviewUpdateRequest(BaseModel):
    status: SenderReviewStatus | None = None
    owner: str | None = None
    notes: str | None = None
```

- [ ] **Step 2: Add the three new repository functions**

Add to the bottom of `backend/app/repositories/dmarc_reports.py` (the existing imports already include `case`, `func`, `select`, `AuthResult`, `DmarcAggregateRecord`, `DmarcAggregateReport` — reuse them, don't re-import):

```python
async def dmarc_summary_totals(db: AsyncSession, domain_id: UUID) -> tuple[int, int]:
    """(total_message_count, dmarc_pass_count) for one domain, all-time. A
    message passes DMARC if EITHER SPF or DKIM is aligned-pass (RFC 7489) —
    policy_evaluated.{dkim,spf} in the aggregate report already reflect the
    receiver's own alignment-aware judgement, so no separate "alignment"
    bookkeeping is needed beyond what's already stored."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    total_count, pass_count = (
        await db.execute(
            select(
                func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
                func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
            ).where(DmarcAggregateRecord.domain_id == domain_id)
        )
    ).one()
    return int(total_count), int(pass_count)


async def dmarc_disposition_breakdown(db: AsyncSession, domain_id: UUID) -> dict[str, int]:
    rows = await db.execute(
        select(DmarcAggregateRecord.disposition, func.sum(DmarcAggregateRecord.count))
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.disposition)
    )
    return {disposition.value: count for disposition, count in rows.all()}


async def latest_published_policy_for_domain(db: AsyncSession, domain_id: UUID) -> str | None:
    return (
        await db.execute(
            select(DmarcAggregateReport.policy_p)
            .where(DmarcAggregateReport.domain_id == domain_id)
            .order_by(DmarcAggregateReport.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
```

- [ ] **Step 3: Migrate the router**

At the top of `backend/app/routers/dmarc_reports.py`, replace the `_get_owned_domain` function and its imports. The new import block (replacing the old `app.models.domain` + no-repository-imports state):

```python
from app.repositories import dmarc_reports as dmarc_reports_repo
from app.repositories.domains import get_owned_domain
```

Delete the entire `_get_owned_domain` function (old lines 41-45). Every remaining call site in the file (`await _get_owned_domain(db, domain_id, user.organization_id)`) becomes `await get_owned_domain(db, domain_id, user.organization_id)` — this touches every endpoint in the file, not just this task's three; make the replacement file-wide now so later tasks never see the old name.

Rewrite the three endpoints:

```python
@router.get("/domains/{domain_id}/dmarc/summary")
async def dmarc_summary(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    await get_owned_domain(db, domain_id, user.organization_id)

    total_count, pass_count = await dmarc_reports_repo.dmarc_summary_totals(db, domain_id)
    by_disposition = await dmarc_reports_repo.dmarc_disposition_breakdown(db, domain_id)
    report_count = await dmarc_reports_repo.count_reports_for_domain(db, domain_id)
    current_policy = await dmarc_reports_repo.latest_published_policy_for_domain(db, domain_id)

    return {
        "total_message_count": total_count,
        "dmarc_pass_count": pass_count,
        "dmarc_fail_count": total_count - pass_count,
        "by_disposition": by_disposition,
        "report_count": report_count,
        "current_policy": current_policy,
    }


@router.get("/domains/{domain_id}/rating")
async def domain_rating(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    domain = await get_owned_domain(db, domain_id, user.organization_id)

    if domain.verification_status != DomainVerificationStatus.verified:
        return {"not_verified": True, "insufficient_data": True, "score": None, "grade": None, "factors": []}

    rating, _total = await compute_domain_rating(db, domain)
    return {
        "not_verified": False,
        "insufficient_data": rating.insufficient_data,
        "score": rating.score,
        "grade": rating.grade,
        "factors": [
            {"factor": f.factor, "weight": f.weight, "score_pct": f.score_pct, "detail": f.detail} for f in rating.factors
        ],
    }


@router.get("/domains/{domain_id}/dmarc/sources")
async def dmarc_sources(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Per-sending-service breakdown (not per raw source_ip — header_from is
    constant per domain so grouping by it, as this endpoint used to, added
    nothing; source_ip alone is the real grouping key, further rolled up by
    identified sending service)."""
    await get_owned_domain(db, domain_id, user.organization_id)
    services = await service_breakdown(db, domain_id)
    await db.commit()  # persists any newly-resolved source_ip_identities cache rows
    return services
```

Note: `report_count.scalar_one()` in the original `dmarc_summary` was a leftover `Result` object being used directly — `count_reports_for_domain` already returns a plain `int`, so the new version doesn't need that `.scalar_one()` call. Confirm this by reading `count_reports_for_domain`'s existing return type (`int`) in the repository file before writing the test.

- [ ] **Step 4: Write the test file with local seed helpers**

```python
# backend/tests/routers/test_dmarc_reports.py
import uuid
from datetime import datetime, timedelta, timezone

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus, UserRole

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org, *, name: str = "example.com", **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name=name, **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def _add_aggregate_report(
    owner_factory,
    org,
    domain,
    *,
    policy_p: str | None = "quarantine",
    date_range_begin: datetime | None = None,
    org_name: str = "google.com",
    report_id: str | None = None,
) -> DmarcAggregateReport:
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        report = DmarcAggregateReport(
            organization_id=org.id,
            domain_id=domain.id if domain is not None else None,
            report_id=report_id or str(uuid.uuid4()),
            org_name=org_name,
            date_range_begin=date_range_begin or now - timedelta(days=1),
            date_range_end=date_range_begin + timedelta(days=1) if date_range_begin else now,
            policy_published_domain=domain.name if domain is not None else "unmatched.example",
            policy_p=policy_p,
            received_at=now,
        )
        db.add(report)
        await db.flush()
        await db.refresh(report)
        await db.commit()
        return report


async def _add_aggregate_record(
    owner_factory,
    org,
    domain,
    report,
    *,
    source_ip: str = "203.0.113.10",
    count: int = 10,
    disposition: Disposition = Disposition.none,
    spf_result: AuthResult = AuthResult.pass_,
    dkim_result: AuthResult = AuthResult.pass_,
    header_from: str | None = None,
) -> DmarcAggregateRecord:
    async with owner_factory() as db:
        record = DmarcAggregateRecord(
            organization_id=org.id,
            report_id=report.id,
            domain_id=domain.id if domain is not None else None,
            source_ip=source_ip,
            count=count,
            disposition=disposition,
            dkim_result=dkim_result,
            spf_result=spf_result,
            header_from=header_from or (domain.name if domain is not None else "unmatched.example"),
            auth_results={},
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        await db.flush()
        await db.refresh(record)
        await db.commit()
        return record


async def test_dmarc_summary_no_reports(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_message_count"] == 0
    assert body["dmarc_pass_count"] == 0
    assert body["dmarc_fail_count"] == 0
    assert body["by_disposition"] == {}
    assert body["report_count"] == 0
    assert body["current_policy"] is None


async def test_dmarc_summary_with_records(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain, policy_p="quarantine")
    await _add_aggregate_record(owner_factory, org, domain, report, count=8, disposition=Disposition.none, spf_result=AuthResult.pass_, dkim_result=AuthResult.fail)
    await _add_aggregate_record(owner_factory, org, domain, report, count=2, disposition=Disposition.reject, spf_result=AuthResult.fail, dkim_result=AuthResult.fail)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_message_count"] == 10
    assert body["dmarc_pass_count"] == 8  # spf pass counts even though dkim failed
    assert body["dmarc_fail_count"] == 2
    assert body["by_disposition"] == {"none": 8, "reject": 2}
    assert body["report_count"] == 1
    assert body["current_policy"] == "quarantine"


async def test_dmarc_summary_404_for_other_orgs_domain(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    other_org, _other_user = await seed_org_and_user(owner_factory, entra=True)
    other_domain = await _add_domain(owner_factory, other_org)

    response = await client.get(f"/api/domains/{other_domain.id}/dmarc/summary")

    assert response.status_code == 404


async def test_domain_rating_not_verified(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.pending)

    response = await client.get(f"/api/domains/{domain.id}/rating")

    assert response.status_code == 200
    body = response.json()
    assert body == {"not_verified": True, "insufficient_data": True, "score": None, "grade": None, "factors": []}


async def test_domain_rating_verified_insufficient_data(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)

    response = await client.get(f"/api/domains/{domain.id}/rating")

    assert response.status_code == 200
    body = response.json()
    assert body["not_verified"] is False
    assert body["insufficient_data"] is True
    assert body["score"] is None


async def test_dmarc_sources_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/sources")

    assert response.status_code == 200
    assert response.json() == []
```

- [ ] **Step 5: Run the new tests**

Run: `pytest tests/routers/test_dmarc_reports.py -v`
Expected: all 6 tests PASS. `test_dmarc_sources_empty` must not hit the DNS landmine — it passes an empty `source_ip` list into `identify_many` (no records seeded), which short-circuits on `if not ips: return {}` before any DNS lookup happens, so no monkeypatch is needed yet for this specific test.

- [ ] **Step 6: Run the full suite to confirm no regressions**

Run: `pytest -v`
Expected: all previously-passing tests still PASS, plus the 6 new ones.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/dmarc_reports.py backend/app/repositories/dmarc_reports.py backend/app/routers/dmarc_reports.py backend/tests/routers/test_dmarc_reports.py
git commit -m "Extract dmarc_reports summary/rating/sources onto schema/repository layers"
```

---

### Task 2: Sender inventory (GET + PATCH) and the DNS-identification test fixture

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add sender-review functions)
- Modify: `backend/app/routers/dmarc_reports.py` (migrate `sender_inventory`, `update_sender_review`; import the schema from Task 1 instead of the inline `BaseModel`)
- Modify: `backend/tests/routers/test_dmarc_reports.py` (add a DNS-lookup-patching fixture + sender-inventory tests)

**Interfaces:**
- Consumes: `app.schemas.dmarc_reports.SenderReviewUpdateRequest` (Task 1). `app.services.dmarc_analytics.service_breakdown(db, domain_id, *, since=None) -> list[dict]` (existing, each dict has key `"service_label"` among others — unchanged).
- Produces: `app.repositories.dmarc_reports.list_sender_reviews_for_domain(db, domain_id) -> Sequence[SenderReview]`, `.upsert_missing_sender_reviews(db, organization_id, domain_id, service_labels) -> None`, `.get_sender_review(db, domain_id, service_label) -> SenderReview | None`, `.create_sender_review(db, *, organization_id, domain_id, service_label) -> SenderReview` — all consumed only within this task.

- [ ] **Step 1: Add the sender-review repository functions**

Add to `backend/app/repositories/dmarc_reports.py`. The file currently has `from uuid import UUID` at the top — change that line to `from uuid import UUID, uuid4` (the code below calls bare `uuid4()`, not `uuid.uuid4()`). Also add `from sqlalchemy.dialects.postgresql import insert as pg_insert` and `from app.models.sender_review import SenderReview`.

```python
async def list_sender_reviews_for_domain(db: AsyncSession, domain_id: UUID) -> Sequence[SenderReview]:
    result = await db.execute(select(SenderReview).where(SenderReview.domain_id == domain_id))
    return result.scalars().all()


async def upsert_missing_sender_reviews(
    db: AsyncSession, organization_id: UUID, domain_id: UUID, service_labels: list[str]
) -> None:
    """Lazily creates a pending SenderReview row for every label in
    `service_labels` that doesn't already have one. ON CONFLICT DO NOTHING
    rather than get-then-insert — two orgs' (or two tabs') page-loads racing
    on the same (domain_id, service_label) shouldn't 500 on the unique
    constraint, same race-tolerant spirit as identify_many's cache upsert."""
    if not service_labels:
        return
    stmt = pg_insert(SenderReview).values(
        [
            {
                "id": uuid4(),
                "organization_id": organization_id,
                "domain_id": domain_id,
                "service_label": label,
                "status": SenderReviewStatus.pending.value,
            }
            for label in service_labels
        ]
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["domain_id", "service_label"])
    await db.execute(stmt)
    await db.flush()


async def get_sender_review(db: AsyncSession, domain_id: UUID, service_label: str) -> SenderReview | None:
    result = await db.execute(
        select(SenderReview).where(SenderReview.domain_id == domain_id, SenderReview.service_label == service_label)
    )
    return result.scalar_one_or_none()


async def create_sender_review(db: AsyncSession, *, organization_id: UUID, domain_id: UUID, service_label: str) -> SenderReview:
    review = SenderReview(organization_id=organization_id, domain_id=domain_id, service_label=service_label)
    db.add(review)
    return review
```

`SenderReviewStatus` needs importing too: add `from app.models.enums import AuthResult, SenderReviewStatus` (extending the existing `from app.models.enums import AuthResult` line rather than adding a second import line for the same module).

- [ ] **Step 2: Migrate the router**

Replace the inline `class SenderReviewUpdateRequest(BaseModel)` with an import: `from app.schemas.dmarc_reports import SenderReviewUpdateRequest`. Remove the now-unused `from pydantic import BaseModel` import only if nothing else in the file still uses `BaseModel` directly (check before deleting — grep the file for `BaseModel` after this edit).

Keep `_sender_review_out` exactly as-is (it's a tiny, router-local response-shaping helper, not a query — no need to move it).

```python
@router.get("/domains/{domain_id}/dmarc/sender-inventory")
async def sender_inventory(
    domain_id: uuid.UUID,
    days: int | None = Query(None, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Richer sibling of /dmarc/sources: the same per-service breakdown,
    joined against sender_reviews for approval status/owner/notes. Any
    service seen here with no existing review row gets one lazily
    upserted with status=pending — this doubles as "new sender" detection
    via the row's created_at, no separate first-seen tracking needed.

    `days` windows to senders with traffic in the last N days, so retired
    senders/IPs (a decommissioned host) drop out of the view; omitted = all-time."""
    await get_owned_domain(db, domain_id, user.organization_id)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    services = await service_breakdown(db, domain_id, since=since)
    if not services:
        return []

    review_rows = await dmarc_reports_repo.list_sender_reviews_for_domain(db, domain_id)
    reviews_by_label = {r.service_label: r for r in review_rows}

    missing_labels = [s["service_label"] for s in services if s["service_label"] not in reviews_by_label]
    if missing_labels:
        await dmarc_reports_repo.upsert_missing_sender_reviews(db, user.organization_id, domain_id, missing_labels)
        review_rows = await dmarc_reports_repo.list_sender_reviews_for_domain(db, domain_id)
        reviews_by_label = {r.service_label: r for r in review_rows}
    await db.commit()

    return [{**s, **_sender_review_out(reviews_by_label[s["service_label"]])} for s in services]


@router.patch("/domains/{domain_id}/dmarc/sender-inventory/{service_label}")
async def update_sender_review(
    domain_id: uuid.UUID,
    service_label: str,
    body: SenderReviewUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    await get_owned_domain(db, domain_id, user.organization_id)

    review = await dmarc_reports_repo.get_sender_review(db, domain_id, service_label)
    if review is None:
        # The sender-inventory GET hasn't lazily created this row yet (e.g.
        # a client patching straight from action-queue data) — create it
        # here too rather than 404ing on a service that legitimately exists.
        review = await dmarc_reports_repo.create_sender_review(
            db, organization_id=user.organization_id, domain_id=domain_id, service_label=service_label
        )

    if body.status is not None:
        review.status = body.status
        review.reviewed_by = user.id
        review.reviewed_at = datetime.now(timezone.utc)
    if body.owner is not None:
        review.owner = body.owner
    if body.notes is not None:
        review.notes = body.notes

    await db.flush()
    await db.refresh(review)
    await db.commit()
    return {"service_label": review.service_label, **_sender_review_out(review)}
```

- [ ] **Step 3: Add the DNS-lookup fixture and sender-inventory tests**

Add near the top of `backend/tests/routers/test_dmarc_reports.py`, after the imports:

```python
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def _fast_ip_fallback(monkeypatch):
    """Every source_ip in this file resolves via identify_many, which does a
    real reverse-DNS lookup through app.services.dns_checks.resolver — that
    module talks to a hostname ("resolver") that only exists on the real
    Docker network. Unpatched, the first call in the whole test run raises
    socket.gaierror. Patching resolve_ptr to return None (matching the
    "no PTR record" branch _resolve_one already handles) makes every source_ip
    resolve to a deterministic ip_fallback identity (service_label == the IP
    itself) with no network I/O at all — same idiom as
    tests/services/source_identification/test_forward_confirm.py."""
    from app.services.source_identification import service_identifier

    async def _no_ptr(ip: str) -> str | None:
        return None

    monkeypatch.setattr(service_identifier, "resolve_ptr", _no_ptr)
```

Add the sender-inventory tests:

```python
async def test_sender_inventory_lazily_creates_pending_reviews(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, source_ip="203.0.113.10", count=5)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/sender-inventory")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["service_label"] == "203.0.113.10"  # ip_fallback label == the IP
    assert body[0]["status"] == "pending"
    assert body[0]["owner"] is None


async def test_sender_inventory_second_call_reuses_existing_review(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, source_ip="203.0.113.10")

    first = await client.get(f"/api/domains/{domain.id}/dmarc/sender-inventory")
    await client.patch(
        f"/api/domains/{domain.id}/dmarc/sender-inventory/203.0.113.10", json={"status": "approved", "owner": "IT"}
    )
    second = await client.get(f"/api/domains/{domain.id}/dmarc/sender-inventory")

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()[0]["status"] == "approved"
    assert second.json()[0]["owner"] == "IT"


async def test_update_sender_review_creates_row_when_missing(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)

    response = await client.patch(
        f"/api/domains/{domain.id}/dmarc/sender-inventory/some-service", json={"status": "blocked"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["service_label"] == "some-service"
    assert body["status"] == "blocked"
    assert body["reviewed_at"] is not None


async def test_update_sender_review_requires_org_admin(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)

    response = await client.patch(
        f"/api/domains/{domain.id}/dmarc/sender-inventory/some-service", json={"status": "approved"}
    )

    assert response.status_code == 403
```

Check `UserRole.member` is the correct non-admin enum value by grepping `app/models/enums.py`'s `UserRole` before running — use whatever the actual non-admin member role is named if different.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/routers/test_dmarc_reports.py -v`
Expected: all 10 tests (6 from Task 1 + 4 new) PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/routers/dmarc_reports.py backend/tests/routers/test_dmarc_reports.py
git commit -m "Extract dmarc_reports sender-inventory endpoints; add DNS-lookup test fixture"
```

---

### Task 3: Trend and posture endpoints

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add trend/posture query functions)
- Modify: `backend/app/routers/dmarc_reports.py` (migrate `dmarc_trend`, `dmarc_posture`)
- Modify: `backend/tests/routers/test_dmarc_reports.py` (add trend/posture tests)

**Interfaces:**
- Consumes: `app.repositories.dmarc_reports.last_report_received_at_for_domain`, `.last_report_received_at_for_org`, `.count_reports_for_domain` (all pre-existing, unchanged). `app.services.rating.domain_rating.compute_domain_rating(db, domain) -> tuple[DomainRating, int]`, `.domain_policy_readiness(db, domain, rating=None, total_volume=None) -> PolicyReadiness` (existing, unchanged).
- Produces: `app.repositories.dmarc_reports.dmarc_trend_by_day(db, organization_id, *, domain_id, since) -> Sequence[Row]`, `.failed_message_volume_for_org_since(db, organization_id, since, *, domain_id=None) -> int`, `.count_new_pending_senders_since(db, organization_id, since, *, domain_id=None) -> int` — consumed only within this task.

- [ ] **Step 1: Add the repository functions**

Add to `backend/app/repositories/dmarc_reports.py`. New import needed: `from app.models.enums import Disposition` (extend the existing `from app.models.enums import AuthResult, SenderReviewStatus` line to `from app.models.enums import AuthResult, Disposition, SenderReviewStatus`), and `from app.models.sender_review import SenderReview` is already imported from Task 2.

```python
async def dmarc_trend_by_day(
    db: AsyncSession, organization_id: UUID, *, domain_id: UUID | None, since: datetime
) -> Sequence:
    """One row per calendar day: total volume, dmarc-pass volume, spf-aligned
    volume, dkim-aligned volume, rejected volume — the /dmarc/trend chart's
    entire data source in one grouped query."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    def _sum_where(condition):
        return func.coalesce(func.sum(case((condition, DmarcAggregateRecord.count), else_=0)), 0)

    day = func.date_trunc("day", DmarcAggregateReport.date_range_begin)
    query = (
        select(
            day.label("day"),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            _sum_where(dmarc_pass),
            _sum_where(DmarcAggregateRecord.spf_result == AuthResult.pass_),
            _sum_where(DmarcAggregateRecord.dkim_result == AuthResult.pass_),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.reject),
        )
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(
            DmarcAggregateRecord.organization_id == organization_id,
            DmarcAggregateReport.date_range_begin >= since,
        )
        .group_by(day)
        .order_by(day)
    )
    if domain_id is not None:
        query = query.where(DmarcAggregateRecord.domain_id == domain_id)
    return (await db.execute(query)).all()


async def failed_message_volume_for_org_since(
    db: AsyncSession, organization_id: UUID, since: datetime, *, domain_id: UUID | None = None
) -> int:
    """Distinct from failed_message_volume_for_domain above: that one is
    domain-scoped/all-time (used by the Domains list card); this is
    org-wide-or-domain-scoped AND date-windowed, for /dmarc/posture."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    query = select(func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)).where(
        DmarcAggregateRecord.organization_id == organization_id
    )
    if domain_id is not None:
        query = query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateRecord.domain_id == domain_id, DmarcAggregateReport.date_range_begin >= since)
    else:
        query = query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateReport.date_range_begin >= since)
    return (await db.execute(query)).scalar_one()


async def count_new_pending_senders_since(
    db: AsyncSession, organization_id: UUID, since: datetime, *, domain_id: UUID | None = None
) -> int:
    query = select(func.count()).select_from(SenderReview).where(
        SenderReview.organization_id == organization_id,
        SenderReview.status == SenderReviewStatus.pending,
        SenderReview.created_at >= since,
    )
    if domain_id is not None:
        query = query.where(SenderReview.domain_id == domain_id)
    return (await db.execute(query)).scalar_one()
```

- [ ] **Step 2: Migrate the router**

```python
@router.get("/dmarc/trend")
async def dmarc_trend(
    domain_id: uuid.UUID | None = Query(None),
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    if domain_id is not None:
        await get_owned_domain(db, domain_id, user.organization_id)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await dmarc_reports_repo.dmarc_trend_by_day(db, user.organization_id, domain_id=domain_id, since=since)
    return [
        {
            "date": d.date().isoformat(),
            "total": int(total),
            "dmarc_pass": int(pass_count),
            "spf_aligned": int(spf_pass),
            "dkim_aligned": int(dkim_pass),
            "rejected": int(rejected),
        }
        for d, total, pass_count, spf_pass, dkim_pass, rejected in rows
    ]


@router.get("/dmarc/posture")
async def dmarc_posture(
    domain_id: uuid.UUID | None = Query(None),
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Bundled compliance/policy/failed-volume/freshness/new-senders/
    ready-to-enforce, rather than six tiny endpoints — matches the
    "don't over-fragment" instinct already applied in dmarc_summary."""
    if domain_id is not None:
        domains = [await get_owned_domain(db, domain_id, user.organization_id)]
    else:
        domains = await list_domains_for_org(db, user.organization_id)

    since = datetime.now(timezone.utc) - timedelta(days=days)

    scored: list[tuple[float, int]] = []
    policy_counts: dict[str, int] = {}
    single_domain_policy: str | None = None
    ready_to_enforce_count = 0

    for domain in domains:
        rating: DomainRating | None = None
        total: int = 0
        if domain.verification_status == DomainVerificationStatus.verified:
            rating, total = await compute_domain_rating(db, domain)
            if not rating.insufficient_data and rating.score is not None:
                scored.append((rating.score, total))

        readiness = await domain_policy_readiness(db, domain, rating=rating, total_volume=total)
        if domain_id is not None:
            single_domain_policy = readiness.latest_policy
        elif readiness.latest_policy is not None:
            policy_counts[readiness.latest_policy] = policy_counts.get(readiness.latest_policy, 0) + 1
        if readiness.ready:
            ready_to_enforce_count += 1

    compliance_pct: float | None = None
    if scored:
        # Weight by volume, but a domain with zero volume in-window still
        # gets a small nonzero weight (1) rather than being dropped from the
        # average entirely.
        weighted_sum = sum(score * max(total, 1) for score, total in scored)
        weight_total = sum(max(total, 1) for _, total in scored)
        compliance_pct = round(weighted_sum / weight_total, 1)

    failed_volume = await dmarc_reports_repo.failed_message_volume_for_org_since(
        db, user.organization_id, since, domain_id=domain_id
    )
    if domain_id is not None:
        last_received_at = await dmarc_reports_repo.last_report_received_at_for_domain(db, domain_id)
    else:
        last_received_at = await dmarc_reports_repo.last_report_received_at_for_org(db, user.organization_id)
    new_sender_count = await dmarc_reports_repo.count_new_pending_senders_since(
        db, user.organization_id, since, domain_id=domain_id
    )

    report_freshness_hours = (
        round((datetime.now(timezone.utc) - last_received_at).total_seconds() / 3600, 1)
        if last_received_at is not None
        else None
    )

    return {
        "compliance_pct": compliance_pct,
        "current_policy": single_domain_policy,
        "policy_distribution": policy_counts if domain_id is None else None,
        "failed_volume": int(failed_volume),
        "report_freshness_hours": report_freshness_hours,
        "new_sender_count": int(new_sender_count),
        "ready_to_enforce_count": ready_to_enforce_count,
    }
```

This needs one more repository import: `list_domains_for_org` from `app.repositories.domains` (already exists there per Task 1's investigation — add it to the `from app.repositories.domains import get_owned_domain` line, making it `from app.repositories.domains import get_owned_domain, list_domains_for_org`).

- [ ] **Step 3: Add trend/posture tests**

```python
async def test_dmarc_trend_buckets_by_day(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    day = datetime.now(timezone.utc) - timedelta(days=2)
    report = await _add_aggregate_report(owner_factory, org, domain, date_range_begin=day)
    await _add_aggregate_record(owner_factory, org, domain, report, count=6, spf_result=AuthResult.pass_, dkim_result=AuthResult.pass_)
    await _add_aggregate_record(owner_factory, org, domain, report, count=4, disposition=Disposition.reject, spf_result=AuthResult.fail, dkim_result=AuthResult.fail)

    response = await client.get("/api/dmarc/trend")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["total"] == 10
    assert body[0]["dmarc_pass"] == 6
    assert body[0]["rejected"] == 4


async def test_dmarc_posture_no_domains(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/dmarc/posture")

    assert response.status_code == 200
    body = response.json()
    assert body["compliance_pct"] is None
    assert body["failed_volume"] == 0
    assert body["report_freshness_hours"] is None
    assert body["new_sender_count"] == 0
    assert body["ready_to_enforce_count"] == 0


async def test_dmarc_posture_reports_freshness_and_failed_volume(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, count=3, spf_result=AuthResult.fail, dkim_result=AuthResult.fail)

    response = await client.get(f"/api/dmarc/posture?domain_id={domain.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["failed_volume"] == 3
    assert body["report_freshness_hours"] is not None
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/routers/test_dmarc_reports.py -v`
Expected: all 13 tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/routers/dmarc_reports.py backend/tests/routers/test_dmarc_reports.py
git commit -m "Extract dmarc_reports trend/posture endpoints onto the repository layer"
```

---

### Task 4: Reports listing — `_apply_report_filters`, by-day, and record detail

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add `_apply_report_filters`, by-day, record-detail)
- Modify: `backend/app/routers/dmarc_reports.py` (migrate `dmarc_reports_by_day`, `dmarc_record_detail`)
- Modify: `backend/tests/routers/test_dmarc_reports.py` (add by-day/record-detail tests)

**Interfaces:**
- Consumes: `app.services.source_identification.service_identifier.identify_many(db, ips) -> dict[str, SourceIdentity]` (existing, unchanged; `SourceIdentity.service_label` is what's read).
- Produces: `app.repositories.dmarc_reports._apply_report_filters(query, *, since, disposition, spf_result, dkim_result, reporter, source_ip)` (module-private, consumed by Task 5 too — Task 5's brief carries this exact signature). `.list_report_records_by_day(db, domain_id, *, limit, before_id, since, disposition, spf_result, dkim_result, reporter, source_ip) -> Sequence[Row]`. `.get_record_detail(db, domain_id, record_id) -> tuple[DmarcAggregateRecord, DmarcAggregateReport] | None`.

- [ ] **Step 1: Add `_apply_report_filters` and the two query functions**

Add to `backend/app/repositories/dmarc_reports.py`. New imports: `from sqlalchemy import tuple_` (extend the existing `from sqlalchemy import case, func, select` to `from sqlalchemy import case, func, select, tuple_`), and `from app.models.enums import Disposition, AuthResult, SenderReviewStatus` already covers `Disposition`/`AuthResult` from Task 3 — no new enum import needed.

```python
def _apply_report_filters(
    query,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
):
    """Shared WHERE-clause vocabulary for the reports/by-day, /summary and
    /grouped endpoints, applied to a query already joined to both
    DmarcAggregateReport and DmarcAggregateRecord."""
    if since is not None:
        query = query.where(DmarcAggregateReport.date_range_begin >= since)
    if disposition is not None:
        query = query.where(DmarcAggregateRecord.disposition == disposition)
    if spf_result is not None:
        query = query.where(DmarcAggregateRecord.spf_result == spf_result)
    if dkim_result is not None:
        query = query.where(DmarcAggregateRecord.dkim_result == dkim_result)
    if reporter is not None:
        query = query.where(DmarcAggregateReport.org_name.ilike(f"%{reporter}%"))
    if source_ip is not None:
        query = query.where(func.host(DmarcAggregateRecord.source_ip) == source_ip)
    return query


async def list_report_records_by_day(
    db: AsyncSession,
    domain_id: UUID,
    *,
    limit: int,
    before_id: UUID | None,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
) -> Sequence:
    """Row granularity is one DmarcAggregateRecord (one sending host within
    one report), not one whole report. Keyset-paginated on
    (date_range_begin, record id) rather than offset, so pages stay stable
    as new reports keep arriving between requests. Filters apply to the
    keyset query itself, not after the fact, since a busy domain can have
    thousands of records."""
    query = (
        select(
            DmarcAggregateRecord.id,
            DmarcAggregateReport.id.label("report_pk"),
            DmarcAggregateReport.org_name,
            DmarcAggregateReport.date_range_begin,
            DmarcAggregateRecord.source_ip,
            DmarcAggregateRecord.count,
            DmarcAggregateRecord.disposition,
            DmarcAggregateRecord.spf_result,
            DmarcAggregateRecord.dkim_result,
        )
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )

    if before_id is not None:
        anchor = (
            await db.execute(
                select(DmarcAggregateReport.date_range_begin, DmarcAggregateRecord.id)
                .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
                .where(DmarcAggregateRecord.id == before_id, DmarcAggregateRecord.domain_id == domain_id)
            )
        ).first()
        if anchor is not None:
            query = query.where(tuple_(DmarcAggregateReport.date_range_begin, DmarcAggregateRecord.id) < anchor)

    query = query.order_by(DmarcAggregateReport.date_range_begin.desc(), DmarcAggregateRecord.id.desc()).limit(limit)
    return (await db.execute(query)).all()


async def get_record_detail(
    db: AsyncSession, domain_id: UUID, record_id: UUID
) -> tuple[DmarcAggregateRecord, DmarcAggregateReport] | None:
    return (
        await db.execute(
            select(DmarcAggregateRecord, DmarcAggregateReport)
            .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
            .where(DmarcAggregateRecord.id == record_id, DmarcAggregateRecord.domain_id == domain_id)
        )
    ).first()
```

- [ ] **Step 2: Migrate the router**

Delete the router's own `_apply_report_filters` function entirely (it moved). Update the import block to add `import itertools` is already present at the top of the file — no change needed there.

```python
@router.get("/domains/{domain_id}/dmarc/reports/by-day")
async def dmarc_reports_by_day(
    domain_id: uuid.UUID,
    limit: int = Query(300, ge=1, le=1000),
    before_id: uuid.UUID | None = Query(None),
    days: int | None = Query(None, ge=1, le=365),
    disposition: Disposition | None = Query(None),
    spf_result: AuthResult | None = Query(None),
    dkim_result: AuthResult | None = Query(None),
    reporter: str | None = Query(None),
    source_ip: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    await get_owned_domain(db, domain_id, user.organization_id)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    rows = await dmarc_reports_repo.list_report_records_by_day(
        db, domain_id, limit=limit, before_id=before_id, since=since, disposition=disposition,
        spf_result=spf_result, dkim_result=dkim_result, reporter=reporter, source_ip=source_ip,
    )

    # Resolve every distinct source_ip on this page to its identified
    # sending service (same cache-first lookup service_breakdown uses), so
    # each row can show "SMTP2GO" next to the raw address instead of the
    # bare IP alone.
    identities = await identify_many(db, [str(r.source_ip) for r in rows])
    await db.commit()

    days_out = []
    for date, group_iter in itertools.groupby(rows, key=lambda r: r.date_range_begin.date()):
        group = list(group_iter)
        report_ids = {r.report_pk for r in group}
        days_out.append(
            {
                "date": date.isoformat(),
                "report_count": len(report_ids),
                "message_count": sum(r.count for r in group),
                "accepted": sum(r.count for r in group if r.disposition == Disposition.none),
                "quarantined": sum(r.count for r in group if r.disposition == Disposition.quarantine),
                "rejected": sum(r.count for r in group if r.disposition == Disposition.reject),
                "rows": [
                    {
                        "record_id": str(r.id),
                        "org_name": r.org_name,
                        "source_ip": str(r.source_ip),
                        "service_label": identities[str(r.source_ip)].service_label,
                        "count": r.count,
                        "disposition": r.disposition.value,
                        "spf_result": r.spf_result.value,
                        "dkim_result": r.dkim_result.value,
                    }
                    for r in group
                ],
            }
        )

    return {"days": days_out, "has_more": len(rows) == limit}


@router.get("/domains/{domain_id}/dmarc/records/{record_id}")
async def dmarc_record_detail(
    domain_id: uuid.UUID,
    record_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    await get_owned_domain(db, domain_id, user.organization_id)

    row = await dmarc_reports_repo.get_record_detail(db, domain_id, record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
    record, report = row

    return {
        "id": str(record.id),
        "report": {
            "id": str(report.id),
            "report_id": report.report_id,
            "org_name": report.org_name,
            "email": report.email,
            "date_range_begin": report.date_range_begin.isoformat(),
            "date_range_end": report.date_range_end.isoformat(),
            "policy_p": report.policy_p,
            "policy_sp": report.policy_sp,
            "policy_pct": report.policy_pct,
            "policy_adkim": report.policy_adkim,
            "policy_aspf": report.policy_aspf,
        },
        "source_ip": str(record.source_ip),
        "count": record.count,
        "disposition": record.disposition.value,
        "spf_result": record.spf_result.value,
        "dkim_result": record.dkim_result.value,
        "header_from": record.header_from,
        "envelope_from": record.envelope_from,
        "envelope_to": record.envelope_to,
        "auth_results": record.auth_results,
        "spf_narrative": spf_narratives(record.auth_results, str(record.source_ip), record.header_from),
        "dkim_narrative": dkim_narratives(record.auth_results, record.header_from),
        "verdict": {
            "spf_aligned": record.spf_result == AuthResult.pass_,
            "dkim_aligned": record.dkim_result == AuthResult.pass_,
            "dmarc_aligned": record.spf_result == AuthResult.pass_ or record.dkim_result == AuthResult.pass_,
            "disposition_applied": record.disposition.value,
        },
    }
```

- [ ] **Step 3: Add by-day/record-detail tests**

```python
async def test_dmarc_reports_by_day_groups_and_paginates(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    day1 = datetime.now(timezone.utc) - timedelta(days=3)
    day2 = datetime.now(timezone.utc) - timedelta(days=1)
    report1 = await _add_aggregate_report(owner_factory, org, domain, date_range_begin=day1)
    report2 = await _add_aggregate_report(owner_factory, org, domain, date_range_begin=day2)
    await _add_aggregate_record(owner_factory, org, domain, report1, count=5)
    await _add_aggregate_record(owner_factory, org, domain, report2, count=7, disposition=Disposition.reject)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/reports/by-day")

    assert response.status_code == 200
    body = response.json()
    assert len(body["days"]) == 2
    assert body["days"][0]["message_count"] == 7  # most recent day first
    assert body["has_more"] is False


async def test_dmarc_reports_by_day_filters_by_disposition(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, count=3, disposition=Disposition.none)
    await _add_aggregate_record(owner_factory, org, domain, report, count=2, disposition=Disposition.reject)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/reports/by-day?disposition=reject")

    assert response.status_code == 200
    body = response.json()
    assert len(body["days"]) == 1
    assert body["days"][0]["message_count"] == 2


async def test_dmarc_record_detail_returns_full_record(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain, policy_p="reject")
    record = await _add_aggregate_record(owner_factory, org, domain, report, source_ip="198.51.100.7", count=4)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/records/{record.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(record.id)
    assert body["source_ip"] == "198.51.100.7"
    assert body["count"] == 4
    assert body["report"]["policy_p"] == "reject"
    assert body["verdict"]["dmarc_aligned"] is True


async def test_dmarc_record_detail_404_for_unknown_record(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/records/{uuid.uuid4()}")

    assert response.status_code == 404
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/routers/test_dmarc_reports.py -v`
Expected: all 17 tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/routers/dmarc_reports.py backend/tests/routers/test_dmarc_reports.py
git commit -m "Extract dmarc_reports by-day and record-detail endpoints; move _apply_report_filters to the repository layer"
```

---

### Task 5: Reports listing — summary and grouped

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add summary/grouped functions + `_GROUPED_BY_COLUMNS`)
- Modify: `backend/app/routers/dmarc_reports.py` (migrate `dmarc_reports_summary`, `dmarc_reports_grouped`)
- Modify: `backend/tests/routers/test_dmarc_reports.py` (add summary/grouped tests)

**Interfaces:**
- Consumes: `app.repositories.dmarc_reports._apply_report_filters` (Task 4, exact signature above).
- Produces: `app.repositories.dmarc_reports.report_totals(db, domain_id, *, since, disposition, spf_result, dkim_result, reporter, source_ip) -> Row`, `.top_failing_source_row(db, domain_id, *, since, disposition, spf_result, dkim_result, reporter, source_ip) -> Row | None`, `._GROUPED_BY_COLUMNS: dict[str, Column]`, `.report_records_grouped(db, domain_id, by, *, since, disposition, spf_result, dkim_result, reporter, source_ip) -> Sequence[Row]` — all consumed only within this task.

- [ ] **Step 1: Add the repository functions**

```python
async def report_totals(
    db: AsyncSession,
    domain_id: UUID,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
):
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    query = (
        select(
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
            func.coalesce(
                func.sum(case((DmarcAggregateRecord.disposition == Disposition.none, DmarcAggregateRecord.count), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((DmarcAggregateRecord.disposition == Disposition.quarantine, DmarcAggregateRecord.count), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((DmarcAggregateRecord.disposition == Disposition.reject, DmarcAggregateRecord.count), else_=0)),
                0,
            ),
            func.count(func.distinct(DmarcAggregateRecord.report_id)),
            func.max(DmarcAggregateReport.received_at),
        )
        .select_from(DmarcAggregateRecord)
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )
    return (await db.execute(query)).one()


async def top_failing_source_row(
    db: AsyncSession,
    domain_id: UUID,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
):
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    failed_sum = func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)
    query = (
        select(DmarcAggregateRecord.source_ip, failed_sum.label("failed"))
        .select_from(DmarcAggregateRecord)
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.source_ip)
        .having(failed_sum > 0)
        .order_by(failed_sum.desc())
        .limit(1)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )
    return (await db.execute(query)).first()


_GROUPED_BY_COLUMNS = {
    "source": DmarcAggregateRecord.source_ip,
    "reporter": DmarcAggregateReport.org_name,
    "disposition": DmarcAggregateRecord.disposition,
}


async def report_records_grouped(
    db: AsyncSession,
    domain_id: UUID,
    by: str,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
) -> Sequence:
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    group_col = _GROUPED_BY_COLUMNS[by]

    def _sum_where(condition):
        return func.coalesce(func.sum(case((condition, DmarcAggregateRecord.count), else_=0)), 0)

    query = (
        select(
            group_col,
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            func.count(func.distinct(DmarcAggregateRecord.report_id)),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.none),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.quarantine),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.reject),
            _sum_where(dmarc_pass),
        )
        .select_from(DmarcAggregateRecord)
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(group_col)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )
    return (await db.execute(query)).all()
```

- [ ] **Step 2: Migrate the router**

Delete the router's own `_GROUPED_BY_COLUMNS` module-level dict (it moved).

```python
@router.get("/domains/{domain_id}/dmarc/reports/summary")
async def dmarc_reports_summary(
    domain_id: uuid.UUID,
    days: int | None = Query(None, ge=1, le=365),
    disposition: Disposition | None = Query(None),
    spf_result: AuthResult | None = Query(None),
    dkim_result: AuthResult | None = Query(None),
    reporter: str | None = Query(None),
    source_ip: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Summary bar for the Reports page — same filter vocabulary as
    by-day/grouped, so switching a filter updates the totals and the rows
    together."""
    await get_owned_domain(db, domain_id, user.organization_id)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    filter_kwargs = dict(
        since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )

    total, passed, accepted, quarantined, rejected, report_count, last_received_at = (
        await dmarc_reports_repo.report_totals(db, domain_id, **filter_kwargs)
    )
    top_failing_row = await dmarc_reports_repo.top_failing_source_row(db, domain_id, **filter_kwargs)

    top_failing_source = None
    if top_failing_row is not None:
        ip_str = str(top_failing_row.source_ip)
        identity = (await identify_many(db, [ip_str]))[ip_str]
        top_failing_source = {
            "source_ip": ip_str,
            "service_label": identity.service_label,
            "failed_count": int(top_failing_row.failed),
        }
    await db.commit()

    return {
        "total_reports": int(report_count),
        "total_messages": int(total),
        "accepted": int(accepted),
        "quarantined": int(quarantined),
        "rejected": int(rejected),
        "dmarc_pass_pct": round(passed / total * 100, 1) if total else None,
        "top_failing_source": top_failing_source,
        "last_report_received_at": last_received_at.isoformat() if last_received_at else None,
    }


@router.get("/domains/{domain_id}/dmarc/reports/grouped")
async def dmarc_reports_grouped(
    domain_id: uuid.UUID,
    by: str = Query(..., pattern="^(source|reporter|disposition)$"),
    days: int | None = Query(None, ge=1, le=365),
    disposition: Disposition | None = Query(None),
    spf_result: AuthResult | None = Query(None),
    dkim_result: AuthResult | None = Query(None),
    reporter: str | None = Query(None),
    source_ip: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """The Reports page's Source/Reporter/Disposition grouping views — small
    cardinality per domain, so one GROUP BY query with no pagination."""
    await get_owned_domain(db, domain_id, user.organization_id)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    rows = await dmarc_reports_repo.report_records_grouped(
        db, domain_id, by, since=since, disposition=disposition, spf_result=spf_result,
        dkim_result=dkim_result, reporter=reporter, source_ip=source_ip,
    )

    identities = {}
    if by == "source":
        identities = await identify_many(db, [str(row[0]) for row in rows])
        await db.commit()

    results = []
    for key_val, volume, report_count, accepted, quarantined, rejected, passed in rows:
        if by == "source":
            key_str = str(key_val)
            label = identities[key_str].service_label
        elif by == "disposition":
            key_str = key_val.value
            label = key_val.value
        else:
            key_str = key_val
            label = key_val
        results.append(
            {
                "key": key_str,
                "label": label,
                "message_count": int(volume),
                "report_count": int(report_count),
                "accepted": int(accepted),
                "quarantined": int(quarantined),
                "rejected": int(rejected),
                "dmarc_pass_pct": round(int(passed) / int(volume) * 100, 1) if volume else None,
            }
        )
    results.sort(key=lambda r: -r["message_count"])
    return results
```

- [ ] **Step 3: Add summary/grouped tests**

```python
async def test_dmarc_reports_summary_totals_and_top_failing(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, source_ip="203.0.113.10", count=8, spf_result=AuthResult.pass_, dkim_result=AuthResult.pass_)
    await _add_aggregate_record(owner_factory, org, domain, report, source_ip="198.51.100.20", count=2, disposition=Disposition.reject, spf_result=AuthResult.fail, dkim_result=AuthResult.fail)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/reports/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_messages"] == 10
    assert body["total_reports"] == 1
    assert body["dmarc_pass_pct"] == 80.0
    assert body["top_failing_source"]["source_ip"] == "198.51.100.20"
    assert body["top_failing_source"]["failed_count"] == 2


async def test_dmarc_reports_summary_no_top_failing_when_all_pass(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, count=5, spf_result=AuthResult.pass_, dkim_result=AuthResult.pass_)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/reports/summary")

    assert response.status_code == 200
    assert response.json()["top_failing_source"] is None


async def test_dmarc_reports_grouped_by_disposition(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, count=6, disposition=Disposition.none)
    await _add_aggregate_record(owner_factory, org, domain, report, count=4, disposition=Disposition.reject)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/reports/grouped?by=disposition")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["key"] == "none"
    assert body[0]["message_count"] == 6
    assert body[1]["key"] == "reject"
    assert body[1]["message_count"] == 4


async def test_dmarc_reports_grouped_by_source(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    report = await _add_aggregate_report(owner_factory, org, domain)
    await _add_aggregate_record(owner_factory, org, domain, report, source_ip="203.0.113.10", count=3)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/reports/grouped?by=source")

    assert response.status_code == 200
    body = response.json()
    assert body[0]["key"] == "203.0.113.10"
    assert body[0]["label"] == "203.0.113.10"  # ip_fallback identity
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/routers/test_dmarc_reports.py -v`
Expected: all 21 tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/routers/dmarc_reports.py backend/tests/routers/test_dmarc_reports.py
git commit -m "Extract dmarc_reports summary and grouped-view endpoints onto the repository layer"
```

---

### Task 6: Detected-domains cluster (unmatched, detected-domains, dismiss)

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add unmatched/detected-domains query functions)
- Modify: `backend/app/routers/dmarc_reports.py` (migrate `unmatched_reports`, `detected_domains`, `dismiss_detected_domain`)
- Modify: `backend/tests/routers/test_dmarc_reports.py` (add detected-domains tests)

**Interfaces:**
- Consumes: nothing new from earlier tasks (this cluster is self-contained aside from `get_owned_domain`, which it doesn't even use — none of these three endpoints take a `domain_id` path param).
- Produces: `app.repositories.dmarc_reports.list_unmatched_aggregate_reports(db, organization_id, limit) -> Sequence[DmarcAggregateReport]`, `.registered_domains_by_name(db, organization_id) -> dict[str, UUID]`, `.dismissed_domain_names(db, organization_id) -> set[str]`, `.unmatched_aggregate_domain_counts(db, organization_id) -> Sequence[Row]`, `.unmatched_record_header_from_counts(db, organization_id) -> Sequence[Row]`, `.unmatched_tls_rpt_domain_counts(db, organization_id) -> Sequence[Row]`, `.unmatched_forensic_domain_counts(db, organization_id) -> Sequence[Row]`, `.dismiss_detected_domain_name(db, *, organization_id, name, dismissed_by) -> None` — all consumed only within this task.

- [ ] **Step 1: Add the repository functions**

New imports needed at the top of `backend/app/repositories/dmarc_reports.py`: `from app.models.dismissed_detected_domain import DismissedDetectedDomain`, `from app.models.dmarc_forensic import DmarcForensicReport`, `from app.models.domain import Domain`, `from app.models.tls_rpt import TlsRptReport`.

```python
async def list_unmatched_aggregate_reports(
    db: AsyncSession, organization_id: UUID, limit: int
) -> Sequence[DmarcAggregateReport]:
    """Aggregate reports whose policy_published domain didn't match any
    registered Domain in this org — surfaced rather than silently dropped
    (see domain_matcher.py)."""
    result = await db.execute(
        select(DmarcAggregateReport)
        .where(DmarcAggregateReport.organization_id == organization_id, DmarcAggregateReport.domain_id.is_(None))
        .order_by(DmarcAggregateReport.received_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def registered_domains_by_name(db: AsyncSession, organization_id: UUID) -> dict[str, UUID]:
    result = await db.execute(select(Domain.id, Domain.name).where(Domain.organization_id == organization_id))
    return {name: domain_id for domain_id, name in result.all()}


async def dismissed_domain_names(db: AsyncSession, organization_id: UUID) -> set[str]:
    result = await db.execute(
        select(DismissedDetectedDomain.name).where(DismissedDetectedDomain.organization_id == organization_id)
    )
    return set(result.scalars().all())


async def unmatched_aggregate_domain_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    result = await db.execute(
        select(
            DmarcAggregateReport.policy_published_domain,
            func.count(func.distinct(DmarcAggregateReport.id)),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
        )
        .outerjoin(DmarcAggregateRecord, DmarcAggregateRecord.report_id == DmarcAggregateReport.id)
        .where(DmarcAggregateReport.organization_id == organization_id, DmarcAggregateReport.domain_id.is_(None))
        .group_by(DmarcAggregateReport.policy_published_domain)
    )
    return result.all()


async def unmatched_record_header_from_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    """A report can match a registered domain (policy_published/domain — e.g.
    the organizational domain, whose policy a subdomain's mail is
    evaluated under) while individual records within it don't — RFC 7489
    §7.2 keeps header_from separate per record for exactly this reason.
    Not filtering by domain_id here (unlike the sibling functions above):
    match_domain's ancestor walk means a record's header_from almost always
    resolves to *some* domain_id once its parent is registered, even though
    header_from itself was never registered — so domain_id IS NULL would
    systematically miss this case. The caller filters out exact registered
    names instead, which is what actually catches it.

    Also excludes records whose source_ip is already reviewed and marked
    "blocked" for the domain_id they resolved to — same idiom
    domain_rating.py's _windowed_totals uses for the rating itself, just
    correlated per-row instead of pinned to one domain_id, since each
    header_from here can resolve to a different ancestor. A sender the org
    has already dealt with (confirmed spoofing/abuse) shouldn't keep
    prompting "add this domain" forever."""
    blocked_source_ips_for_row = (
        select(SourceIpIdentity.source_ip)
        .join(SenderReview, SenderReview.service_label == SourceIpIdentity.service_label)
        .where(
            SenderReview.domain_id == DmarcAggregateRecord.domain_id,
            SenderReview.status == SenderReviewStatus.blocked,
        )
        .correlate(DmarcAggregateRecord)
    )
    result = await db.execute(
        select(
            DmarcAggregateRecord.header_from,
            func.count(func.distinct(DmarcAggregateRecord.report_id)),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
        )
        .where(
            DmarcAggregateRecord.organization_id == organization_id,
            DmarcAggregateRecord.source_ip.not_in(blocked_source_ips_for_row),
        )
        .group_by(DmarcAggregateRecord.header_from)
    )
    return result.all()


async def unmatched_tls_rpt_domain_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    result = await db.execute(
        select(
            TlsRptReport.policy_domain,
            func.count(func.distinct(TlsRptReport.id)),
            func.coalesce(func.sum(TlsRptReport.summary_success_count + TlsRptReport.summary_failure_count), 0),
        )
        .where(TlsRptReport.organization_id == organization_id, TlsRptReport.domain_id.is_(None))
        .group_by(TlsRptReport.policy_domain)
    )
    return result.all()


async def unmatched_forensic_domain_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    result = await db.execute(
        select(DmarcForensicReport.reported_domain, func.count())
        .where(
            DmarcForensicReport.organization_id == organization_id,
            DmarcForensicReport.domain_id.is_(None),
            DmarcForensicReport.reported_domain.is_not(None),
            DmarcForensicReport.reported_domain != "",
        )
        .group_by(DmarcForensicReport.reported_domain)
    )
    return result.all()


async def dismiss_detected_domain_name(db: AsyncSession, *, organization_id: UUID, name: str, dismissed_by: UUID) -> None:
    """ON CONFLICT DO NOTHING rather than add()-then-catch: dismissing an
    already-dismissed name (e.g. a retried click) is a no-op, not an
    error — same race-tolerant idiom as sender_inventory's SenderReview
    upsert."""
    stmt = pg_insert(DismissedDetectedDomain).values(
        organization_id=organization_id, name=name, dismissed_by=dismissed_by
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["organization_id", "name"])
    await db.execute(stmt)
```

- [ ] **Step 2: Migrate the router**

`unmatched_reports` and `dismiss_detected_domain` become thin. `detected_domains` keeps its Python-side assembly (ancestor-suffix matching, cross-source merging, sorting) inline in the router — that logic operates on already-fetched dicts, not the database, and is specific to this one endpoint's response shape.

```python
@router.get("/dmarc/unmatched")
async def unmatched_reports(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Aggregate reports whose policy_published domain didn't match any
    registered Domain in this org — surfaced rather than silently dropped
    (see domain_matcher.py)."""
    reports = await dmarc_reports_repo.list_unmatched_aggregate_reports(db, user.organization_id, limit)
    return [
        {
            "id": str(r.id),
            "org_name": r.org_name,
            "report_id": r.report_id,
            "policy_published_domain": r.policy_published_domain,
            "received_at": r.received_at.isoformat(),
        }
        for r in reports
    ]


@router.get("/dmarc/detected-domains")
async def detected_domains(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Distinct domain names seen in reports that didn't match anything
    registered — the same unmatched bucket as /dmarc/unmatched, but grouped
    by domain name (across aggregate, TLS-RPT, and forensic reports) so it
    reads as "domains you could add" rather than a raw report list. Each
    entry is annotated with whether it looks like a subdomain of a domain
    you've already registered (in which case "Add" can create it correctly
    parented in one step) or of another *detected* domain (informational —
    our one-level nesting model means the detected apex should generally be
    added first)."""
    detected: dict[str, dict] = {}

    # Needed up front (not just for the suggested-parent annotation below):
    # match_domain's ancestor walk means a subdomain of an already-registered
    # domain always resolves to that ancestor's domain_id, never NULL — so a
    # domain_id IS NULL filter alone would never catch it. Filtering out
    # exact registered names instead (further down) is what actually surfaces
    # that case, e.g. a subdomain sending real mail whose parent is
    # registered but which itself never was, exactly the gap that left
    # a real customer subdomain invisible until its mail started bouncing.
    registered = await dmarc_reports_repo.registered_domains_by_name(db, user.organization_id)
    registered_names = set(registered.keys())

    dismissed_names = await dmarc_reports_repo.dismissed_domain_names(db, user.organization_id)

    for name, report_count, message_volume in await dmarc_reports_repo.unmatched_aggregate_domain_counts(
        db, user.organization_id
    ):
        detected[name] = {"report_count": report_count, "message_volume": int(message_volume)}

    for name, report_count, message_volume in await dmarc_reports_repo.unmatched_record_header_from_counts(
        db, user.organization_id
    ):
        if name in registered_names:
            continue
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count
        entry["message_volume"] += int(message_volume)

    for name, report_count, message_volume in await dmarc_reports_repo.unmatched_tls_rpt_domain_counts(
        db, user.organization_id
    ):
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count
        entry["message_volume"] += int(message_volume)

    for name, report_count in await dmarc_reports_repo.unmatched_forensic_domain_counts(db, user.organization_id):
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count

    for name in dismissed_names:
        detected.pop(name, None)

    detected_names = set(detected.keys())

    items = []
    for name, stats in detected.items():
        suggested_parent_id: str | None = None
        suggested_parent_name: str | None = None
        relationship = "apex"

        # Prefer the longest (most specific) registered ancestor, in case
        # more than one registered domain is a suffix match.
        for reg_name, reg_id in sorted(registered.items(), key=lambda kv: -len(kv[0])):
            if name.endswith(f".{reg_name}"):
                suggested_parent_id = str(reg_id)
                suggested_parent_name = reg_name
                relationship = "subdomain_of_registered"
                break

        if suggested_parent_id is None:
            for other in sorted(detected_names, key=len):
                if other != name and name.endswith(f".{other}"):
                    relationship = "subdomain_of_detected"
                    suggested_parent_name = other
                    break

        items.append(
            {
                "name": name,
                "report_count": stats["report_count"],
                "message_volume": stats["message_volume"],
                "relationship": relationship,
                "suggested_parent_id": suggested_parent_id,
                "suggested_parent_name": suggested_parent_name,
            }
        )

    # Apex-looking entries first, then shorter (more likely-apex) names first.
    items.sort(key=lambda x: (x["relationship"] != "apex", len(x["name"]), -x["report_count"]))
    return items


@router.post("/dmarc/detected-domains/{name}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_detected_domain(
    name: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> None:
    """Marks a detected-but-not-registered name as "not mine" so it stops
    surfacing — for lookalikes, unrelated senders, or anything else the org
    has looked at and decided isn't worth registering as a domain."""
    name = name.strip().lower().rstrip(".")
    await dmarc_reports_repo.dismiss_detected_domain_name(
        db, organization_id=user.organization_id, name=name, dismissed_by=user.id
    )
    await db.commit()
```

- [ ] **Step 3: Add detected-domains tests**

```python
async def test_unmatched_reports_lists_only_domainless_reports(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    await _add_aggregate_report(owner_factory, org, domain, org_name="matched-reporter.com")
    await _add_aggregate_report(owner_factory, org, None, org_name="unmatched-reporter.com")

    response = await client.get("/api/dmarc/unmatched")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["org_name"] == "unmatched-reporter.com"


async def test_detected_domains_surfaces_unregistered_header_from(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    report = await _add_aggregate_report(owner_factory, org, None, org_name="reporter.com")
    await _add_aggregate_record(owner_factory, org, None, report, header_from="spoofed.example", count=15)

    response = await client.get("/api/dmarc/detected-domains")

    assert response.status_code == 200
    body = response.json()
    names = {item["name"] for item in body}
    assert "spoofed.example" in names
    entry = next(item for item in body if item["name"] == "spoofed.example")
    assert entry["message_volume"] == 15
    assert entry["relationship"] == "apex"


async def test_detected_domains_flags_subdomain_of_registered(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    parent = await _add_domain(owner_factory, org, name="example.com")
    report = await _add_aggregate_report(owner_factory, org, None, org_name="reporter.com")
    await _add_aggregate_record(owner_factory, org, None, report, header_from="mail.example.com", count=5)

    response = await client.get("/api/dmarc/detected-domains")

    assert response.status_code == 200
    entry = next(item for item in response.json() if item["name"] == "mail.example.com")
    assert entry["relationship"] == "subdomain_of_registered"
    assert entry["suggested_parent_id"] == str(parent.id)


async def test_dismiss_detected_domain_removes_it_and_is_idempotent(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)
    report = await _add_aggregate_report(owner_factory, org, None, org_name="reporter.com")
    await _add_aggregate_record(owner_factory, org, None, report, header_from="lookalike.example", count=3)

    dismiss_response = await client.post("/api/dmarc/detected-domains/lookalike.example/dismiss")
    assert dismiss_response.status_code == 204

    after = await client.get("/api/dmarc/detected-domains")
    assert "lookalike.example" not in {item["name"] for item in after.json()}

    repeat_dismiss = await client.post("/api/dmarc/detected-domains/lookalike.example/dismiss")
    assert repeat_dismiss.status_code == 204  # idempotent, not a 409/500
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/routers/test_dmarc_reports.py -v`
Expected: all 25 tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/routers/dmarc_reports.py backend/tests/routers/test_dmarc_reports.py
git commit -m "Extract dmarc_reports detected-domains endpoints onto the repository layer"
```

---

### Task 7: Policy-recommendation engine extraction and the policy-builder endpoint

**Files:**
- Create: `backend/app/services/rating/policy_recommendation.py`
- Create: `backend/tests/services/rating/test_policy_recommendation.py`
- Modify: `backend/app/routers/dmarc_reports.py` (delete the moved functions; migrate `dmarc_policy_builder`)
- Modify: `backend/tests/routers/test_dmarc_reports.py` (add one HTTP-level policy-builder test)

**Interfaces:**
- Consumes: `app.services.rating.domain_rating.compute_domain_rating`, `.domain_policy_readiness`, `.policy_stability_days`, `.READY_TO_ENFORCE_MIN_PASS_PCT` (all existing, unchanged). `app.services.rating.score.DomainRating` (existing). `app.services.action_queue.rules.reviewed_service_labels(db, domain_id) -> set[str]`, `.unreviewed_high_volume_senders(services, reviewed_labels) -> list[dict]` (existing, unchanged). `app.repositories.mailbox_connections.get_org_mailbox_connection(db, organization_id) -> MailboxConnection | None` (existing).
- Produces: `app.services.rating.policy_recommendation.build_policy_recommendation(*, domain, rating, total, readiness, stability_days, blockers) -> dict` — this is the router's only remaining call into this module.

- [ ] **Step 1: Create the policy-recommendation service module**

```python
# backend/app/services/rating/policy_recommendation.py
"""Pure, DB-free policy-recommendation engine for the DMARC policy-builder
UI — takes already-computed rating/readiness/blocker data (from
domain_rating.py and action_queue/rules.py) and turns it into one concrete
next-policy recommendation with human-readable reasoning. No queries here;
this is business logic, same category as score.py's compute_rating."""

from app.models.domain import Domain
from app.models.enums import DomainMailProfile
from app.services.rating.domain_rating import READY_TO_ENFORCE_MIN_PASS_PCT, PolicyReadiness
from app.services.rating.score import DomainRating

POLICY_STABILITY_MIN_DAYS = 14


def build_policy_recommendation(
    *,
    domain: Domain,
    rating: DomainRating,
    total: int,
    readiness: PolicyReadiness,
    stability_days: int,
    blockers: list[dict],
) -> dict:
    """DMARCbis-compliant (RFC 9989 Appendix A.6 removes pct= entirely, so
    this never generates or recommends one) — mirrors the two worked
    examples from the product spec almost verbatim: a blocked domain gets
    told exactly which sender/volume is holding it back, a ready domain
    gets told exactly why it's ready. Staged rollout is handled by gating
    the policy MOVE itself on pass-rate + stability + no unreviewed
    senders, rather than by publishing a partial-enforcement percentage —
    the safety net current-generation pct= provided is now this app's own
    report analysis instead.

    Also recommends np= (RFC 9989's new non-existent-subdomain policy,
    distinct from sp= which covers subdomains that exist but lack their
    own DMARC record) — see _recommend_np below for why this is decided
    independently of whether the main policy move itself is blocked."""
    recommendation = _build_base_recommendation(
        domain=domain, rating=rating, total=total, readiness=readiness, stability_days=stability_days, blockers=blockers
    )
    recommendation["np"] = _recommend_np(recommendation["policy"])
    return recommendation


def _recommend_np(recommended_policy: str) -> str | None:
    """Non-existent subdomains can never have a legitimate sender by
    definition, so hardening them (np=reject) is safe regardless of how
    cautious the main policy rollout is being — recommended as soon as
    quarantine or reject is in play anywhere in the recommendation, not
    gated behind the same pass-rate/blocker checks the main policy is."""
    return "reject" if recommended_policy in ("quarantine", "reject") else None


def _build_base_recommendation(
    *,
    domain: Domain,
    rating: DomainRating,
    total: int,
    readiness: PolicyReadiness,
    stability_days: int,
    blockers: list[dict],
) -> dict:
    current_policy = readiness.latest_policy

    # A domain with no legitimate outbound mail has nothing to wait for —
    # rating.insufficient_data would otherwise always be true here (no
    # senders means no report volume, permanently) and recommend staying at
    # monitor-only forever. Checked first, ahead of that branch.
    if domain.mail_profile != DomainMailProfile.sends_mail:
        label = "receive-only" if domain.mail_profile == DomainMailProfile.receive_only else "not used for mail"
        return {
            "policy": "reject",
            "reasoning": (
                f"This domain is marked {label} — there's no legitimate outbound mail to protect, so lock down to "
                "p=reject now rather than waiting on report data that won't arrive."
            ),
            "blocked": False,
            "blocking_reason": None,
        }

    if rating.insufficient_data:
        return {
            "policy": "none",
            "reasoning": "No reports yet — start at monitor-only until data arrives.",
            "blocked": False,
            "blocking_reason": None,
        }

    if blockers:
        blockers_sorted = sorted(blockers, key=lambda s: -s["volume"])
        top = blockers_sorted[0]
        extra = f" and {len(blockers_sorted) - 1} other unreviewed sender(s)" if len(blockers_sorted) > 1 else ""
        blocking_reason = (
            f'{top["volume"]} messages from an unreviewed sender ("{top["service_label"]}"){extra} — '
            "review or approve them in Sender Inventory before tightening enforcement."
        )
        return {
            "policy": current_policy or "none",
            "reasoning": f"Stay at {f'p={current_policy}' if current_policy else 'monitor-only'} for now.",
            "blocked": True,
            "blocking_reason": blocking_reason,
        }

    pass_rate_factor = next((f for f in rating.factors if f.factor == "dmarc_pass_rate"), None)
    pass_rate_pct = pass_rate_factor.score_pct if pass_rate_factor is not None else None

    if pass_rate_pct is None or pass_rate_pct < READY_TO_ENFORCE_MIN_PASS_PCT:
        failed = round(total * (1 - (pass_rate_pct or 0) / 100))
        return {
            "policy": current_policy or "none",
            "reasoning": (
                f"Stay at {f'p={current_policy}' if current_policy else 'monitor-only'} for now. {failed} of "
                f"{total} messages are failing DMARC ({round(100 - (pass_rate_pct or 0), 1)}% fail rate) — fix "
                "or approve the senders responsible before enforcing."
            ),
            "blocked": True,
            "blocking_reason": f"DMARC pass rate is {pass_rate_pct or 0}%, below the {READY_TO_ENFORCE_MIN_PASS_PCT}% bar.",
        }

    if current_policy is None or current_policy == "none":
        return {
            "policy": "quarantine",
            "reasoning": (
                f"This domain looks ready for p=quarantine. DMARC pass rate is {pass_rate_pct}%, no unreviewed "
                "high-volume senders were seen, and reports are arriving normally."
            ),
            "blocked": False,
            "blocking_reason": None,
        }

    if current_policy == "quarantine":
        if stability_days >= POLICY_STABILITY_MIN_DAYS:
            return {
                "policy": "reject",
                "reasoning": (
                    f"Stable at p=quarantine for {stability_days} days with a {pass_rate_pct}% pass rate — "
                    "ready to move to p=reject."
                ),
                "blocked": False,
                "blocking_reason": None,
            }
        return {
            "policy": "quarantine",
            "reasoning": (
                f"Pass rate looks good ({pass_rate_pct}%), but p=quarantine has only been stable for "
                f"{stability_days} day(s) so far — give it a bit longer before moving to p=reject."
            ),
            "blocked": False,
            "blocking_reason": None,
        }

    return {
        "policy": "reject",
        "reasoning": "Already at the strongest policy (p=reject) — nothing further to recommend.",
        "blocked": False,
        "blocking_reason": None,
    }
```

- [ ] **Step 2: Write pure unit tests for the recommendation engine**

```python
# backend/tests/services/rating/test_policy_recommendation.py
from app.models.domain import Domain
from app.models.enums import DomainMailProfile
from app.services.rating.domain_rating import PolicyReadiness
from app.services.rating.policy_recommendation import build_policy_recommendation
from app.services.rating.score import DomainRating, RatingFactor


def _domain(mail_profile=DomainMailProfile.sends_mail) -> Domain:
    return Domain(name="example.com", mail_profile=mail_profile)


def _rating(insufficient_data=False, pass_rate_pct=None) -> DomainRating:
    factors = []
    if pass_rate_pct is not None:
        factors.append(RatingFactor(factor="dmarc_pass_rate", weight=40, score_pct=pass_rate_pct, detail=""))
    return DomainRating(score=None, grade=None, insufficient_data=insufficient_data, factors=factors)


def _readiness(latest_policy=None) -> PolicyReadiness:
    return PolicyReadiness(
        eligible=True, ready=False, latest_policy=latest_policy, next_rung=None, pass_rate_pct=None, total_volume=0
    )


def test_receive_only_domain_recommends_reject_regardless_of_data():
    domain = _domain(mail_profile=DomainMailProfile.receive_only)
    rec = build_policy_recommendation(
        domain=domain, rating=_rating(insufficient_data=True), total=0, readiness=_readiness(), stability_days=0, blockers=[]
    )
    assert rec["policy"] == "reject"
    assert rec["blocked"] is False
    assert rec["np"] == "reject"


def test_parked_domain_recommends_reject():
    domain = _domain(mail_profile=DomainMailProfile.parked)
    rec = build_policy_recommendation(
        domain=domain, rating=_rating(insufficient_data=True), total=0, readiness=_readiness(), stability_days=0, blockers=[]
    )
    assert rec["policy"] == "reject"
    assert "not used for mail" in rec["reasoning"]


def test_insufficient_data_recommends_monitor_only():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(insufficient_data=True), total=0, readiness=_readiness(), stability_days=0, blockers=[]
    )
    assert rec["policy"] == "none"
    assert rec["blocked"] is False
    assert rec["np"] is None


def test_blockers_present_reports_top_offender_and_stays_put():
    blockers = [
        {"service_label": "shadow-it.example", "volume": 500},
        {"service_label": "small-sender.example", "volume": 10},
    ]
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.0), total=1000, readiness=_readiness(latest_policy="none"),
        stability_days=0, blockers=blockers,
    )
    assert rec["blocked"] is True
    assert "shadow-it.example" in rec["blocking_reason"]
    assert "1 other unreviewed sender" in rec["blocking_reason"]
    assert rec["policy"] == "none"


def test_low_pass_rate_blocks_with_fail_count_in_reasoning():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=80.0), total=100, readiness=_readiness(latest_policy="none"),
        stability_days=0, blockers=[],
    )
    assert rec["blocked"] is True
    assert "20 of 100 messages" in rec["reasoning"]
    assert rec["policy"] == "none"


def test_ready_domain_at_none_recommends_quarantine():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.5), total=1000, readiness=_readiness(latest_policy="none"),
        stability_days=0, blockers=[],
    )
    assert rec["policy"] == "quarantine"
    assert rec["blocked"] is False
    assert rec["np"] == "reject"


def test_quarantine_not_yet_stable_stays_at_quarantine():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.5), total=1000, readiness=_readiness(latest_policy="quarantine"),
        stability_days=5, blockers=[],
    )
    assert rec["policy"] == "quarantine"
    assert "5 day(s)" in rec["reasoning"]


def test_quarantine_stable_long_enough_recommends_reject():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.5), total=1000, readiness=_readiness(latest_policy="quarantine"),
        stability_days=14, blockers=[],
    )
    assert rec["policy"] == "reject"
    assert rec["blocked"] is False


def test_already_at_reject_has_nothing_further_to_recommend():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.9), total=1000, readiness=_readiness(latest_policy="reject"),
        stability_days=60, blockers=[],
    )
    assert rec["policy"] == "reject"
    assert "nothing further" in rec["reasoning"]
    assert rec["np"] == "reject"
```

- [ ] **Step 3: Run the new unit tests**

Run: `pytest tests/services/rating/test_policy_recommendation.py -v`
Expected: all 9 tests PASS, no database/HTTP involved (plain sync test functions — no `api` fixture, matches `tests/services/rating/test_score.py`'s existing style).

- [ ] **Step 4: Migrate the router's `dmarc_policy_builder` endpoint**

Delete the old `POLICY_STABILITY_MIN_DAYS`, `_build_policy_recommendation`, `_recommend_np`, `_build_base_recommendation` from `backend/app/routers/dmarc_reports.py` — they moved. Update imports: remove `from app.services.rating.domain_rating import (READY_TO_ENFORCE_MIN_PASS_PCT, PolicyReadiness, compute_domain_rating, domain_policy_readiness, policy_stability_days)` and replace with `from app.services.rating.domain_rating import compute_domain_rating, domain_policy_readiness, policy_stability_days` (the router no longer needs `READY_TO_ENFORCE_MIN_PASS_PCT`/`PolicyReadiness` directly — those only matter inside the moved recommendation engine, and `domain_rating`/`dmarc_posture` from Tasks 1/3 use `compute_domain_rating`/`domain_policy_readiness` without needing those two). Add `from app.services.rating.policy_recommendation import build_policy_recommendation`. Add `from app.repositories.mailbox_connections import get_org_mailbox_connection`.

```python
@router.get("/domains/{domain_id}/dmarc/policy-builder")
async def dmarc_policy_builder(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """Bundles everything the policy-builder UI needs in one call: the live
    current record, whether rua= actually reaches the connected mailbox,
    and a recommended next record — built entirely from data this app
    already computes elsewhere (compute_domain_rating, domain_policy_readiness,
    service_breakdown+sender_reviews), not new analysis."""
    domain = await get_owned_domain(db, domain_id, user.organization_id)

    connection = await get_org_mailbox_connection(db, user.organization_id)
    # A domain with its own hosted address (see POST
    # /domains/{id}/hosted-report-address) is using that as its dedicated
    # report destination — prefer it over the org's shared MailboxConnection,
    # which local-auth orgs never have at all.
    mailbox_address = domain.hosted_report_address or (connection.mailbox_address if connection is not None else None)

    lookup_error = False
    current: DmarcRecordInfo | None
    try:
        current = await fetch_current_dmarc_record(domain.name)
    except DnsLookupError:
        current = None
        lookup_error = True

    if mailbox_address is not None:
        rua_result = await check_rua_destination(domain.name, mailbox_address)
        rua_destination = {"status": rua_result.status, "current_targets": rua_result.current_targets}
    else:
        rua_destination = {"status": "no_mailbox", "current_targets": []}

    rating, total = await compute_domain_rating(db, domain)
    readiness = await domain_policy_readiness(db, domain, rating=rating, total_volume=total)
    stability_days = await policy_stability_days(db, domain.id)

    services = await service_breakdown(db, domain.id)
    reviewed_labels = await reviewed_service_labels(db, domain.id)
    blockers = unreviewed_high_volume_senders(services, reviewed_labels)
    await db.commit()  # persists identify_many's cache upsert from service_breakdown, see its own docstring

    recommendation = build_policy_recommendation(
        domain=domain, rating=rating, total=total, readiness=readiness, stability_days=stability_days, blockers=blockers
    )

    return {
        "current_record": {"raw": current.raw, "tags": current.tags} if current is not None else None,
        "current_record_lookup_error": lookup_error,
        "rua_destination": rua_destination,
        "org_mailbox_address": mailbox_address,
        "hosted_report_address": domain.hosted_report_address,
        "policy_stability_days": stability_days,
        "recommendation": recommendation,
    }
```

- [ ] **Step 5: Add one HTTP-level policy-builder test**

The recommendation engine's branches are already covered by Task 7's pure unit tests — this one HTTP test only needs to prove the endpoint wires everything together correctly, not re-cover every branch.

This test calls `fetch_current_dmarc_record("example.com")`, which does a real DNS TXT lookup through the same `resolver` hostname landmine as `identify_many`. Unlike `identify_many`, this one isn't fixed by the file's `_fast_ip_fallback` fixture (that only patches `resolve_ptr`) — it needs its own patch.

`app/routers/dmarc_reports.py` imports this function by direct name (`from app.services.dns_checks.dmarc_record import DmarcRecordInfo, check_rua_destination, fetch_current_dmarc_record`, confirmed at that file's line 26) — same "direct name import" shape Plan A2's ledger already hit with `entra_oidc` vs. `async_session_factory`: monkeypatching `app.services.dns_checks.dmarc_record.fetch_current_dmarc_record` would NOT affect the already-bound name inside `app.routers.dmarc_reports`'s own namespace. Patch the router's namespace directly:

```python
async def test_dmarc_policy_builder_insufficient_data_recommends_monitor_only(api, monkeypatch):
    from app.services.dns_checks.dmarc_record import DnsLookupError

    async def _raise_lookup_error(domain_name: str):
        raise DnsLookupError("no resolver in test env")

    monkeypatch.setattr("app.routers.dmarc_reports.fetch_current_dmarc_record", _raise_lookup_error)

    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/policy-builder")

    assert response.status_code == 200
    body = response.json()
    assert body["current_record_lookup_error"] is True
    assert body["recommendation"]["policy"] == "none"
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/routers/test_dmarc_reports.py tests/services/rating/test_policy_recommendation.py -v`
Expected: 26 router tests + 9 service tests, all PASS.

- [ ] **Step 7: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/rating/policy_recommendation.py backend/tests/services/rating/test_policy_recommendation.py backend/app/routers/dmarc_reports.py backend/tests/routers/test_dmarc_reports.py
git commit -m "Extract the DMARC policy-recommendation engine into services/rating; migrate the policy-builder endpoint"
```

---

### Task 8: Final verification pass

**Files:**
- Read (no modification expected): `backend/app/routers/dmarc_reports.py`, `backend/app/repositories/dmarc_reports.py`, `backend/app/repositories/README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task is verification and documentation-consistency only.

- [ ] **Step 1: Confirm the router has no leftover inline SQL**

Run: `grep -n "select(\|db.execute(\|pg_insert(" backend/app/routers/dmarc_reports.py`
Expected: no matches. Every query now lives in `app/repositories/dmarc_reports.py` or a called service function.

- [ ] **Step 2: Confirm no stray `_get_owned_domain` references remain anywhere in the codebase**

Run: `grep -rn "_get_owned_domain" backend/`
Expected: no matches (the router's own copy was deleted in Task 1; no other file ever defined one after Plan A's earlier dedup).

- [ ] **Step 3: Confirm `app/repositories/README.md` still describes this file accurately**

Read `backend/app/repositories/README.md`. It already names `repositories/dmarc_reports.py` as "for the two `DmarcAggregateReport`/`DmarcAggregateRecord` models" — that description still holds (the file gained many more functions but no new models), so no edit should be needed. If the self-review finds the description has actually gone stale (e.g. it enumerates specific function names elsewhere in the file that this plan's functions contradict), fix it — but check before assuming a change is needed.

- [ ] **Step 4: Confirm `app/services/README.md` still holds for the `rating/` subpackage**

Read `backend/app/services/README.md`. It already names `rating/` as an example subpackage requiring 2+ files — it now has three (`domain_rating.py`, `score.py`, `policy_recommendation.py`), which still satisfies the existing description with no wording change needed.

- [ ] **Step 5: Run the full test suite one final time**

Run: `pytest -v`
Expected: every test in the suite passes, including the 26 new router tests and 9 new service tests introduced by this plan (35 new tests total across Tasks 1-7).

- [ ] **Step 6: Manually smoke-test against a live container (optional but recommended given this plan's DNS-lookup landmine only exists in the test suite's mocked form)**

If a throwaway Postgres + built image is available (same pattern as Plan A/A2's final verification), start the stack and hit `GET /api/domains/{id}/dmarc/sender-inventory` and `GET /api/domains/{id}/dmarc/policy-builder` for a domain with at least one real ingested report, confirming `service_label` resolves to something sensible (either a real PTR-derived label or a bare-IP `ip_fallback`) rather than erroring — this is the one code path this plan's mocked tests can't fully prove end-to-end, since production DOES have a working `resolver` container.

- [ ] **Step 7: Commit if Step 3 or 4 required doc edits**

```bash
git add backend/app/repositories/README.md backend/app/services/README.md
git commit -m "Update repository/service docs after the dmarc_reports extraction"
```

Skip this commit if neither file needed changes.
