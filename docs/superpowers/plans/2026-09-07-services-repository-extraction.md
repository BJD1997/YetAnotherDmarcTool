# Service-Layer Repository Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the cohesion-refactor spec's repository-layer goal — "routers **and services** call repository functions; neither issues raw queries directly anymore" — for the four service files this app's DMARC-reporting/rating/action-queue features depend on: `app/services/rating/domain_rating.py`, `app/services/action_queue/rules.py`, `app/services/source_identification/service_identifier.py`, `app/services/dmarc_analytics.py`. Every routers-layer file is already query-free (see the separate `2026-09-07-dns-checks-cohesion-refactor.md` plan for the last router gap); these four service files are the only remaining place in the backend that still issues `db.execute()` directly outside the repository layer.

**Architecture:** Same pattern as every prior extraction in this sequence: each inline query moves into the repository file for the model/feature area it queries, and the service function that used to build the query now calls the repository function instead — same name, same signature, same callers, same behavior. Two things make this plan different from the router extractions:

1. **Pure duplicate elimination, not just relocation.** Three of the eleven queries being moved are byte-identical to repository functions that *already exist* (two from the `dns_checks`/`mailbox_connections` repositories, one from `dmarc_reports.py`'s Plan A3 work) — those three call sites get pointed at the existing function instead of a new one being created. This plan is worth doing specifically because it removes real, confirmed duplication, not just code motion for its own sake.
2. **A genuine layering hazard to avoid.** `service_identifier.py` defines its own `SourceIdentity` dataclass (distinct from — and easily confused with — the `SourceIpIdentity` *model* in `app/models/source_ip_identity.py`). The naive move would have the new repository function's signature take `dict[str, SourceIdentity]`, which would make `app/repositories/` import a type defined in `app/services/` — backwards, since services depend on repositories, never the other way around. Task 4 below designs around this: the repository function's write-side signature takes a plain `list[dict]` (the same shape the service already builds for its `pg_insert(...).values([...])` call), never the service's own dataclass.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Postgres/RLS (unchanged). No new tests are strictly required by this plan for functions already covered by existing HTTP-level router tests (`test_domains.py`, `test_action_queue.py`, `test_dns_checks.py`, `test_dmarc_reports.py` already exercise every function this plan touches indirectly, since it's a pure behavior-preserving move) — each task still adds a direct repository-level test for its new functions, matching this codebase's established convention of testing the repository layer directly, not just through the router.

**Spec:** `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`

## Global Constraints

- Every repository function takes `db: AsyncSession` as its first argument, returns data, and holds no module-level mutable state.
- Every touched service function (`_windowed_totals`, `latest_findings_by_type`, `domain_policy_readiness`, `policy_stability_days`, `reviewed_service_labels`, `mailbox_stopped_receiving_reports`, `spf_lookup_limit_risk`, `identify_many`) **keeps its existing name, signature, and all existing callers unchanged** — only its internal implementation changes, from building/executing a query to calling a repository function. This is a pure behavior-preserving move; no caller anywhere in the codebase should need to change how it invokes any of these functions.
- Repository functions never import types defined under `app/services/` — only `app/models/` types and stdlib/SQLAlchemy types cross that boundary. See Task 4 for the one place this plan would otherwise violate it.
- Preserve every existing inline comment when moving code — several document non-obvious business rules (the blocked-sender exclusion rationale, the FCrDNS cache-freshness rationale, the RFC citations) that must survive the move verbatim or near-verbatim.
- Before creating a new repository function, check whether an existing one already does the exact same query (this plan identifies three such cases already — Tasks 2 and 3 name them explicitly — but re-verify each against the live file before writing new code, in case anything has changed since this plan was written).

---

### Task 1: `dmarc_analytics.py`'s `service_breakdown` query

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add one function)
- Modify: `backend/app/services/dmarc_analytics.py` (replace the inline query with a repository call)
- Create: `backend/tests/repositories/test_dmarc_reports.py` (new — see Step 4; if this file already exists by the time this task runs, add to it instead of creating it)

**Interfaces:**
- Consumes: nothing new from elsewhere.
- Produces: `app.repositories.dmarc_reports.per_source_ip_volume_breakdown(db: AsyncSession, domain_id: UUID, *, since: datetime | None = None) -> Sequence[Row]` — consumed only by this task.

- [ ] **Step 1: Add the repository function**

Add to `backend/app/repositories/dmarc_reports.py` (this file already has `case`, `func`, `select`, `AuthResult`, `Disposition`, `DmarcAggregateRecord`, `DmarcAggregateReport` imported from Plan A3 — reuse them).

```python
async def per_source_ip_volume_breakdown(
    db: AsyncSession, domain_id: UUID, *, since: datetime | None = None
) -> Sequence:
    """Volume/alignment/disposition aggregated per-source-IP for one domain
    — the SQL half of dmarc_analytics.py's service_breakdown (identifying
    each IP's sending service and rolling up by service label happens in
    Python there, not here). `since` windows to reports whose traffic
    period begins on or after it (joining the parent report's
    date_range_begin), so a decommissioned host or a retired sender with
    no recent traffic simply drops out. None = all-time."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    def _sum_where(condition):
        return func.sum(case((condition, DmarcAggregateRecord.count), else_=0))

    query = (
        select(
            DmarcAggregateRecord.source_ip,
            func.sum(DmarcAggregateRecord.count),
            _sum_where(DmarcAggregateRecord.spf_result == AuthResult.pass_),
            _sum_where(DmarcAggregateRecord.dkim_result == AuthResult.pass_),
            _sum_where(dmarc_pass),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.none),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.quarantine),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.reject),
        )
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.source_ip)
    )
    if since is not None:
        query = query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateReport.date_range_begin >= since)
    return (await db.execute(query)).all()
```

- [ ] **Step 2: Update `service_breakdown`**

In `backend/app/services/dmarc_analytics.py`, replace the inline query construction and `await db.execute(query)` call (the `dmarc_pass`/`_sum_where`/`query` block and the `result = await db.execute(query)` line) with a call to the new repository function. The `per_ip` list-comprehension that follows stays exactly as-is, just iterating over the repository function's return value instead of `result.all()`:

```python
async def service_breakdown(db: AsyncSession, domain_id: uuid.UUID, *, since: datetime | None = None) -> list[dict]:
    """Volume/alignment/disposition aggregated per-IP in SQL, then grouped by
    identified service label in Python (simpler and more testable than an
    awkward GROUP BY over a value that only exists after a DNS lookup).

    `since` windows the breakdown to reports whose traffic period begins on or
    after it (joining the parent report's date_range_begin, the same recency
    basis dmarc_trend uses) — so a decommissioned host or a retired sender with
    no recent traffic simply drops out of the inventory. None = all-time.

    Does NOT commit — identify_many's cache upsert is executed but left
    uncommitted, deliberately. A commit here would end the calling request's
    SET LOCAL app.current_org_id scope (see app/db/rls.py), silently
    breaking RLS for any query the caller runs afterward — which several
    callers do (sender_inventory queries sender_reviews right after this;
    the action-queue router calls this once per domain in a loop, and a
    mid-loop commit would drop RLS context for every later iteration).
    Callers must commit once, themselves, after all their own RLS-scoped
    work for the request is done."""
    rows = await per_source_ip_volume_breakdown(db, domain_id, since=since)
    per_ip = [
        {
            "source_ip": str(ip),
            "volume": int(volume),
            "spf_pass": int(spf_pass),
            "dkim_pass": int(dkim_pass),
            "dmarc_pass": int(dmarc_pass_count),
            "accepted": int(accepted),
            "quarantined": int(quarantined),
            "rejected": int(rejected),
        }
        for ip, volume, spf_pass, dkim_pass, dmarc_pass_count, accepted, quarantined, rejected in rows
    ]
    if not per_ip:
        return []
    # ... rest of the function (identify_many call onward) is unchanged
```

Add `from app.repositories.dmarc_reports import per_source_ip_volume_breakdown` to the imports. Remove `from sqlalchemy import case, func, select` if nothing else in the file still uses them directly (check with `grep -n "case(\|func\.\|select(" backend/app/services/dmarc_analytics.py` after the edit — `_is_likely_spoofed`, `_fcrdns_status`, and the rest of `service_breakdown` don't use SQLAlchemy constructs, so this import should become fully removable, but verify rather than assume).

- [ ] **Step 3: Run the existing tests**

Run: `pytest tests/services/test_dmarc_analytics.py tests/routers/test_dmarc_reports.py tests/routers/test_action_queue.py -v`
Expected: all pass unchanged — this is a pure internal refactor of `service_breakdown`, and these three files exercise it both directly (indirectly, since `test_dmarc_analytics.py` only tests the pure helper functions today, not `service_breakdown` itself) and through the HTTP layer (`dmarc_sources`, `sender_inventory`, action-queue rules that call it).

- [ ] **Step 4: Add a direct repository-level test**

`service_breakdown` itself has no direct test today (only its pure helper functions `_fcrdns_status`/`_is_ipv6` do, in `tests/services/test_dmarc_analytics.py`) — its only coverage is indirect, through router tests. Add a new test file `backend/tests/repositories/test_dmarc_reports_analytics.py` (a repository-level test, separate from the router-level `tests/routers/test_dmarc_reports.py`, matching this codebase's directory convention: `tests/repositories/` for direct repository-function tests. If `tests/repositories/` doesn't exist yet as a directory, this is the first file in it — check whether it needs an `__init__.py` by looking at how `tests/routers/` or `tests/services/` are structured):

```python
# backend/tests/repositories/test_dmarc_reports_analytics.py
import uuid
from datetime import datetime, timedelta, timezone

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition
from app.repositories.dmarc_reports import per_source_ip_volume_breakdown

from tests.conftest import seed_org_and_user


async def _add_domain(owner_factory, org, *, name: str = "example.com") -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name=name)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def _add_report_and_record(
    owner_factory, org, domain, *, source_ip: str, count: int, date_range_begin: datetime | None = None
) -> None:
    now = datetime.now(timezone.utc)
    begin = date_range_begin or now - timedelta(days=1)
    async with owner_factory() as db:
        report = DmarcAggregateReport(
            organization_id=org.id,
            domain_id=domain.id,
            report_id=str(uuid.uuid4()),
            org_name="reporter.example",
            date_range_begin=begin,
            date_range_end=begin + timedelta(days=1),
            policy_published_domain=domain.name,
            received_at=now,
        )
        db.add(report)
        await db.flush()
        record = DmarcAggregateRecord(
            organization_id=org.id,
            report_id=report.id,
            domain_id=domain.id,
            source_ip=source_ip,
            count=count,
            disposition=Disposition.none,
            dkim_result=AuthResult.pass_,
            spf_result=AuthResult.pass_,
            header_from=domain.name,
            auth_results={},
            created_at=now,
        )
        db.add(record)
        await db.commit()


async def test_per_source_ip_volume_breakdown_groups_by_ip(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await _add_report_and_record(owner_factory, org, domain, source_ip="203.0.113.10", count=5)
    await _add_report_and_record(owner_factory, org, domain, source_ip="198.51.100.20", count=3)

    async with owner_factory() as db:
        rows = await per_source_ip_volume_breakdown(db, domain.id)

    by_ip = {str(r[0]): r for r in rows}
    assert by_ip["203.0.113.10"][1] == 5
    assert by_ip["198.51.100.20"][1] == 3


async def test_per_source_ip_volume_breakdown_since_filter_excludes_old_traffic(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    old = datetime.now(timezone.utc) - timedelta(days=90)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    await _add_report_and_record(owner_factory, org, domain, source_ip="203.0.113.10", count=5, date_range_begin=old)
    await _add_report_and_record(owner_factory, org, domain, source_ip="198.51.100.20", count=3, date_range_begin=recent)

    async with owner_factory() as db:
        rows = await per_source_ip_volume_breakdown(db, domain.id, since=datetime.now(timezone.utc) - timedelta(days=30))

    ips = {str(r[0]) for r in rows}
    assert ips == {"198.51.100.20"}
```

Note this test uses the `api` fixture purely for its `owner_factory` (RLS-bypassing seed access) and doesn't make any HTTP calls — this matches how other direct-repository tests in this codebase that need seeded data but not the HTTP layer are structured (check `tests/routers/test_dmarc_reports.py`'s own helpers for the established seeding pattern if this doesn't quite match; adjust field names to whatever that file's helpers actually use, since it was the most recently written and is the closest precedent).

- [ ] **Step 5: Run the new tests plus the full suite**

Run: `pytest tests/repositories/ -v` then `pytest -v`
Expected: new tests pass, full suite passes with no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/services/dmarc_analytics.py backend/tests/repositories/
git commit -m "Move dmarc_analytics.py's service_breakdown query onto the repository layer"
```

---

### Task 2: `domain_rating.py`'s four queries

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add 2 functions)
- Modify: `backend/app/repositories/dns_checks.py` (add 1 function)
- Modify: `backend/app/services/rating/domain_rating.py` (replace 4 inline queries with repository calls; reuse 1 existing repository function)
- Modify/Create: `backend/tests/repositories/` (add direct tests for the 2 new `dmarc_reports.py` functions and the 1 new `dns_checks.py` function)

**Interfaces:**
- Consumes: `app.repositories.dmarc_reports.latest_published_policy_for_domain(db, domain_id) -> str | None` (already exists, from Plan A3 — reused here, not recreated).
- Produces: `app.repositories.dmarc_reports.windowed_totals_excluding_blocked(db, domain_id, since) -> tuple[int, int]`, `app.repositories.dns_checks.list_dns_check_results_at_latest_run(db, domain_id) -> dict[CheckType, list[DnsCheckResult]]`, `app.repositories.dmarc_reports.policy_p_by_day_since(db, domain_id, since) -> Sequence[Row]` — all consumed only within this task and Task 3 (see Task 3's Interfaces for which of these it reuses).

- [ ] **Step 1: Add `windowed_totals_excluding_blocked` to `repositories/dmarc_reports.py`**

This replaces `_windowed_totals`'s inline query. New imports needed: `from datetime import timedelta` (extend the existing `from datetime import datetime` import), `from app.models.sender_review import SenderReview` (already imported from Plan A3's Task 2), `from app.models.source_ip_identity import SourceIpIdentity` (already imported from Plan A3's Task 6), `from app.models.enums import SenderReviewStatus` (already imported).

```python
RATING_WINDOW_DAYS = 90


async def windowed_totals_excluding_blocked(db: AsyncSession, domain_id: UUID, since: datetime) -> tuple[int, int]:
    """(total_count, dmarc_pass_count) over the window starting at `since`,
    excluding traffic from senders explicitly marked blocked (SenderReview)
    — confirmed spoofing/abuse a domain owner has already dealt with
    shouldn't keep dragging the rating down forever. Pending/unreviewed
    traffic still counts normally (only an explicit "blocked" excludes).

    Windowed on DmarcAggregateReport.date_range_begin (the report's mail
    period), not DmarcAggregateRecord.created_at (ingestion time) — same
    join/filter idiom dmarc_trend/_apply_report_filters/policy_stability_days
    already use.

    The blocked-sender exclusion is a pure SQL join — source_ip (INET) on
    both DmarcAggregateRecord and the global SourceIpIdentity cache, then
    SourceIpIdentity.service_label against SenderReview.service_label — no
    identify_many() round-trip and no commit needed. A source_ip with no
    cached identity yet simply can't match a blocked service_label, so it's
    conservatively still counted (correct default: only proven-blocked
    traffic drops out)."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    blocked_source_ips = (
        select(SourceIpIdentity.source_ip)
        .join(SenderReview, SenderReview.service_label == SourceIpIdentity.service_label)
        .where(SenderReview.domain_id == domain_id, SenderReview.status == SenderReviewStatus.blocked)
    )
    total_count, pass_count = (
        await db.execute(
            select(
                func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
                func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
            )
            .select_from(DmarcAggregateRecord)
            .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
            .where(
                DmarcAggregateRecord.domain_id == domain_id,
                DmarcAggregateReport.date_range_begin >= since,
                DmarcAggregateRecord.source_ip.not_in(blocked_source_ips),
            )
        )
    ).one()
    return int(total_count), int(pass_count)
```

Note: `RATING_WINDOW_DAYS = 90` is being duplicated here as a constant rather than imported, because `domain_rating.py` needs it too (to compute `since` before calling this function) and a repository file should not import a constant from the service layer it's meant to be depended on BY, not depend ON (same directional-layering rule as the `SourceIdentity` hazard called out in this plan's Architecture section). Keep both copies in sync if this value is ever tuned — a small, accepted duplication in exchange for correct dependency direction. Do not attempt to resolve this by having `domain_rating.py` import `RATING_WINDOW_DAYS` from the repository file — that inverts the dependency the wrong way for a constant that's conceptually a *service*-level policy choice (how far back the rating looks), not a repository concern.

- [ ] **Step 2: Add `list_dns_check_results_at_latest_run` to `repositories/dns_checks.py`**

```python
async def list_dns_check_results_at_latest_run(db: AsyncSession, domain_id: UUID) -> dict:
    """Every DnsCheckResult row from the domain's most recent recheck,
    grouped by check_type. Used by domain_rating.py's compute_domain_rating
    (via latest_findings_by_type) and app/routers/domains.py directly."""
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at)).where(DnsCheckResult.domain_id == domain_id).scalar_subquery()
    )
    check_rows = (
        (
            await db.execute(
                select(DnsCheckResult).where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.checked_at == latest_ts)
            )
        )
        .scalars()
        .all()
    )
    findings_by_type: dict = {}
    for row in check_rows:
        findings_by_type.setdefault(row.check_type, []).append(row)
    return findings_by_type
```

New import needed in `repositories/dns_checks.py`: `from app.models.enums import CheckType` is NOT strictly needed here (the function doesn't reference `CheckType` by name, it just uses whatever `row.check_type` already is as dict keys) — only add it if you choose to annotate the return type precisely as `dict[CheckType, list[DnsCheckResult]]`, which is recommended for consistency with the rest of this codebase's type-annotated repository functions.

- [ ] **Step 3: Add `policy_p_by_day_since` to `repositories/dmarc_reports.py`**

```python
async def policy_p_by_day_since(db: AsyncSession, domain_id: UUID, since: datetime) -> Sequence:
    """(day, policy_p) for every distinct day+policy combination reported
    in the window — the raw material for policy_stability_days' streak
    count, which lives in domain_rating.py (that's business logic, not a
    query). Ordered newest-day-first."""
    day_col = func.date_trunc("day", DmarcAggregateReport.date_range_begin)
    return (
        await db.execute(
            select(day_col, DmarcAggregateReport.policy_p)
            .where(DmarcAggregateReport.domain_id == domain_id, DmarcAggregateReport.date_range_begin >= since)
            .distinct()
            .order_by(day_col.desc())
        )
    ).all()
```

- [ ] **Step 4: Update `domain_rating.py` to call the repository layer**

Add imports: `from app.repositories.dmarc_reports import latest_published_policy_for_domain, policy_p_by_day_since, windowed_totals_excluding_blocked` and `from app.repositories.dns_checks import list_dns_check_results_at_latest_run`.

Rewrite `_windowed_totals` (keep the name, signature, and both callers — `compute_domain_rating` in this file, and `rules.py`'s `high_volume_failure` — completely unchanged):

```python
async def _windowed_totals(db: AsyncSession, domain_id: uuid.UUID) -> tuple[int, int]:
    """(total_count, dmarc_pass_count) over the last RATING_WINDOW_DAYS days,
    excluding traffic from senders explicitly marked blocked (SenderReview)
    — confirmed spoofing/abuse a domain owner has already dealt with
    shouldn't keep dragging the score down forever. Pending/unreviewed
    traffic still counts normally (only an explicit "blocked" excludes)."""
    since = datetime.now(timezone.utc) - timedelta(days=RATING_WINDOW_DAYS)
    return await windowed_totals_excluding_blocked(db, domain_id, since)
```

Rewrite `latest_findings_by_type` (keep the name, signature, and its callers — `compute_domain_rating` in this file, `app/routers/domains.py` — completely unchanged):

```python
async def latest_findings_by_type(db: AsyncSession, domain_id: uuid.UUID) -> dict[CheckType, list[DnsCheckResult]]:
    """Every DnsCheckResult row from the domain's most recent recheck,
    grouped by check_type — the same "latest checked_at" fetch used
    verbatim in app/routers/dns_checks.py's list_latest_checks."""
    return await list_dns_check_results_at_latest_run(db, domain_id)
```

In `domain_policy_readiness`, replace the inline `latest_policy` query with a call to the existing Plan A3 function:

```python
    latest_policy = await latest_published_policy_for_domain(db, domain.id)
```

(This deletes the `select(DmarcAggregateReport.policy_p).where(...).order_by(...).limit(1)` block entirely — the rest of `domain_policy_readiness`'s logic after this line is unchanged.)

In `policy_stability_days`, replace the inline query with a call to the new repository function, keeping the streak-counting loop that follows exactly as-is:

```python
async def policy_stability_days(db: AsyncSession, domain_id: uuid.UUID, max_days: int = 60) -> int:
    """How many consecutive days (counting back from the most recent
    reporting day, within the last `max_days`) every report has agreed on
    the same published policy — a lightweight "has enforcement been
    stable" signal derived entirely from policy_p snapshots already stored
    per report, no new tracking column needed. A day where reports
    disagree on policy_p (e.g. a same-day DNS change) breaks the streak,
    same as a day at a genuinely different policy would."""
    since = datetime.now(timezone.utc) - timedelta(days=max_days)
    rows = await policy_p_by_day_since(db, domain_id, since)
    if not rows:
        return 0

    policies_by_day: dict = {}
    for day, policy in rows:
        policies_by_day.setdefault(day, set()).add(policy)

    current_policy = rows[0][1]
    streak = 0
    for day in sorted(policies_by_day.keys(), reverse=True):
        if policies_by_day[day] != {current_policy}:
            break
        streak += 1
    return streak
```

Remove `from sqlalchemy import case, func, select` if nothing else in `domain_rating.py` still needs them directly after these four changes (check with `grep -n "case(\|func\.\|select(" backend/app/services/rating/domain_rating.py`).

- [ ] **Step 5: Run the existing tests**

Run: `pytest tests/routers/test_domains.py tests/routers/test_dmarc_reports.py tests/routers/test_action_queue.py tests/routers/test_dns_checks.py -v`
Expected: all pass unchanged — every one of `domain_rating.py`'s 4 functions is exercised indirectly by at least one of these files already (`domains.py`'s rating display, `dmarc_reports.py`'s `/rating` and `/policy-builder` endpoints, action-queue's `low_compliance_domain`/`domain_ready_for_stricter_policy`/`enforcement_readiness_notice`/`high_volume_failure`).

- [ ] **Step 6: Add direct repository-level tests**

Add to `backend/tests/repositories/test_dmarc_reports_analytics.py` (created in Task 1) or a new `backend/tests/repositories/test_dns_checks.py` as appropriate — one test per new repository function (`windowed_totals_excluding_blocked`, `list_dns_check_results_at_latest_run`, `policy_p_by_day_since`), each seeding real rows and asserting the returned shape/values directly, following the same pattern as Task 1's tests. Specifically cover `windowed_totals_excluding_blocked`'s blocked-sender exclusion (seed a blocked `SenderReview` + matching `SourceIpIdentity`, confirm that source_ip's volume is excluded from the totals) since that's the one query with real conditional logic worth a dedicated assertion, not just a smoke test.

- [ ] **Step 7: Run the new tests plus the full suite**

Run: `pytest tests/repositories/ -v` then `pytest -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/repositories/dns_checks.py backend/app/services/rating/domain_rating.py backend/tests/repositories/
git commit -m "Move domain_rating.py's four inline queries onto the repository layer"
```

---

### Task 3: `action_queue/rules.py`'s four queries

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py` (add 1 function)
- Modify: `backend/app/repositories/dns_checks.py` (add 1 function)
- Modify: `backend/app/services/action_queue/rules.py` (replace 4 inline queries — 2 with reuse of existing functions, 2 with new ones)
- Modify: `backend/tests/repositories/` (add direct tests for the 2 new functions)

**Interfaces:**
- Consumes: `app.repositories.mailbox_connections.get_org_mailbox_connection(db, organization_id) -> MailboxConnection | None` (already exists), `app.repositories.dmarc_reports.last_report_received_at_for_org(db, organization_id) -> datetime | None` (already exists, from Plan A) — both reused here, not recreated.
- Produces: `app.repositories.dmarc_reports.list_reviewed_service_labels_for_domain(db, domain_id) -> set[str]`, `app.repositories.dns_checks.latest_dns_check_results_of_type_for_domain(db, domain_id, check_type) -> Sequence[DnsCheckResult]` — consumed only within this task.

- [ ] **Step 1: Add `list_reviewed_service_labels_for_domain` to `repositories/dmarc_reports.py`**

```python
async def list_reviewed_service_labels_for_domain(db: AsyncSession, domain_id: UUID) -> set[str]:
    """service_labels with an explicit approved/ignored/blocked review row
    for this domain — a service is "unreviewed" if it's missing here
    entirely (no sender_reviews row at all) or the row is still pending,
    both read the same way by callers so this doesn't depend on the
    lazy-create-on-read timing of the sender-inventory endpoint having
    already run for this domain."""
    result = await db.execute(
        select(SenderReview.service_label).where(
            SenderReview.domain_id == domain_id,
            SenderReview.status.in_(
                [SenderReviewStatus.approved, SenderReviewStatus.ignored, SenderReviewStatus.blocked]
            ),
        )
    )
    return {row[0] for row in result.all()}
```

- [ ] **Step 2: Add `latest_dns_check_results_of_type_for_domain` to `repositories/dns_checks.py`**

New import needed: `from app.models.enums import CheckType`.

```python
async def latest_dns_check_results_of_type_for_domain(
    db: AsyncSession, domain_id: UUID, check_type: CheckType
) -> Sequence[DnsCheckResult]:
    """Directly surfaces the existing finding for one check_type from the
    last check run — used by action_queue/rules.py's spf_lookup_limit_risk.
    Deliberately narrower than list_dns_check_results_at_latest_run (which
    returns every check_type): that function's other caller
    (domain_rating.py, and via it app/routers/domains.py) genuinely needs
    every type at once, but this caller only ever wants one, so a separate
    narrow query avoids fetching and discarding unrelated check types on
    every action-queue evaluation (which runs per-domain, potentially in a
    loop over every domain in an org)."""
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at))
        .where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.check_type == check_type)
        .scalar_subquery()
    )
    result = await db.execute(
        select(DnsCheckResult).where(
            DnsCheckResult.domain_id == domain_id,
            DnsCheckResult.check_type == check_type,
            DnsCheckResult.checked_at == latest_ts,
        )
    )
    return result.scalars().all()
```

- [ ] **Step 3: Update `rules.py`**

Add imports: `from app.repositories.dmarc_reports import last_report_received_at_for_org, list_reviewed_service_labels_for_domain`, `from app.repositories.dns_checks import latest_dns_check_results_of_type_for_domain`, `from app.repositories.mailbox_connections import get_org_mailbox_connection`.

Rewrite `reviewed_service_labels` (keep the name, signature, and every caller in this file unchanged):

```python
async def reviewed_service_labels(db: AsyncSession, domain_id: uuid.UUID) -> set[str]:
    """service_labels with an explicit approved/ignored/blocked review row
    for this domain — a service is "unreviewed" if it's missing here
    entirely (no sender_reviews row at all) or the row is still pending,
    both read the same way by callers so this doesn't depend on the
    lazy-create-on-read timing of the sender-inventory endpoint having
    already run for this domain."""
    return await list_reviewed_service_labels_for_domain(db, domain_id)
```

In `mailbox_stopped_receiving_reports`, replace both inline queries:

```python
    connection = await get_org_mailbox_connection(db, organization_id)
    if connection is None or connection.consent_status != ConsentStatus.granted:
        return []
    if connection.last_sync_status == SyncStatus.error:
        return []  # a failing sync is already surfaced by the mailbox-health widget — don't duplicate
    if connection.last_sync_at is None:
        return []  # never synced yet at all — not "stopped", just not started

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_MAILBOX_DAYS)
    if connection.last_sync_at < cutoff:
        return []  # sync itself hasn't run recently either — a scheduling/worker problem, not this rule

    latest_report_at = await last_report_received_at_for_org(db, organization_id)
    if latest_report_at is not None and latest_report_at >= cutoff:
        return []
```

(The rest of the function — the `ActionItem` construction and return — is unchanged.)

In `spf_lookup_limit_risk`, replace the inline query:

```python
    rows = await latest_dns_check_results_of_type_for_domain(db, domain.id, CheckType.spf)
```

(This replaces the `latest_ts = select(func.max(...))...scalar_subquery()` block and the `rows = (await db.execute(select(DnsCheckResult)...)).scalars().all()` block entirely — the `items = []` loop that follows is unchanged.)

Remove `from sqlalchemy import func, select` if nothing else in `rules.py` still needs them directly (check with `grep -n "func\.\|select("` after all four edits — `DmarcAggregateReport`, `MailboxConnection`, `SenderReview` model imports may also become unused if nothing else in the file references them directly; verify each before removing).

- [ ] **Step 4: Run the existing tests**

Run: `pytest tests/routers/test_action_queue.py -v`
Expected: all pass unchanged — this file's HTTP-level tests exercise every rule function in `rules.py`, since the action-queue router calls all of them per domain.

- [ ] **Step 5: Add direct repository-level tests**

Add tests for `list_reviewed_service_labels_for_domain` and `latest_dns_check_results_of_type_for_domain` to `backend/tests/repositories/`, following the established pattern: seed real `SenderReview`/`DnsCheckResult` rows, call the function directly, assert on the returned data. For `latest_dns_check_results_of_type_for_domain` specifically, cover the "only the requested check_type comes back, even when other types exist for the same domain" case — seed at least two `DnsCheckResult` rows of different `check_type` at the same `checked_at`, request one type, assert the other doesn't appear.

- [ ] **Step 6: Run the new tests plus the full suite**

Run: `pytest tests/repositories/ -v` then `pytest -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py backend/app/repositories/dns_checks.py backend/app/services/action_queue/rules.py backend/tests/repositories/
git commit -m "Move action_queue/rules.py's four inline queries onto the repository layer, reusing two existing repository functions"
```

---

### Task 4: `service_identifier.py`'s two queries (new repository file)

**Files:**
- Create: `backend/app/repositories/source_identification.py`
- Modify: `backend/app/services/source_identification/service_identifier.py`
- Create: `backend/tests/repositories/test_source_identification.py`

**Interfaces:**
- Consumes: nothing new from elsewhere.
- Produces: `app.repositories.source_identification.get_cached_identities(db, ips: list[str]) -> dict[str, SourceIpIdentity]`, `app.repositories.source_identification.upsert_resolved_identities(db, rows: list[dict]) -> None` — consumed only by `identify_many` in this task.

**Layering note (read before starting):** `service_identifier.py` defines its own `SourceIdentity` dataclass (`service_label`, `match_method`, `ptr_hostname`, `fcrdns_valid`) — distinct from, and easily confused with, the `SourceIpIdentity` *model* in `app/models/source_ip_identity.py`. The read-side function below returns `SourceIpIdentity` model instances (fine — models are a legitimate repository-layer type). The write-side function does **not** take `dict[str, SourceIdentity]`, even though that's the shape `identify_many` naturally has in hand — repositories must not import a type from `app/services/`. Instead, `upsert_resolved_identities` takes a plain `list[dict]` with the exact keys the upsert needs, and `identify_many` builds that list itself before calling it (it already builds almost this exact structure today for its `pg_insert(...).values([...])` call — this task moves that dict-building into the service function, unchanged, and hands the result to the repository instead of building the `pg_insert` statement itself).

- [ ] **Step 1: Create the repository file**

```python
# backend/app/repositories/source_identification.py
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_ip_identity import SourceIpIdentity


async def get_cached_identities(db: AsyncSession, ips: list[str]) -> dict[str, SourceIpIdentity]:
    """Cache rows for any of `ips` already resolved, keyed by the bare IP
    string. source_ip is INET; comparing it against a Python str list needs
    some cast, but CAST(inet AS text) (unlike inet's default display
    format) includes a "/32" or "/128" netmask suffix and would never match
    a bare address string — confirmed directly against this table's real
    data. host(inet) returns the bare address as text with no such suffix.

    asyncpg round-trips INET columns as ipaddress.IPv4Address/IPv6Address,
    not str — normalize to str so lookups against the plain-string `ips`
    argument actually hit (an IPv4Address key would never equal a str key)."""
    if not ips:
        return {}
    rows = (
        (await db.execute(select(SourceIpIdentity).where(func.host(SourceIpIdentity.source_ip).in_(ips))))
        .scalars()
        .all()
    )
    return {str(row.source_ip): row for row in rows}


async def upsert_resolved_identities(db: AsyncSession, rows: list[dict]) -> None:
    """`rows` entries must have exactly the keys source_ip, ptr_hostname,
    service_label, match_method, fcrdns_valid, resolved_at — the caller
    (service_identifier.py's identify_many) builds this list from its own
    SourceIdentity dataclass instances; this function stays primitive
    (plain dicts, no service-layer types) so the repository layer never
    imports from app/services/."""
    if not rows:
        return
    stmt = pg_insert(SourceIpIdentity).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SourceIpIdentity.source_ip],
        set_={
            "ptr_hostname": stmt.excluded.ptr_hostname,
            "service_label": stmt.excluded.service_label,
            "match_method": stmt.excluded.match_method,
            "fcrdns_valid": stmt.excluded.fcrdns_valid,
            "resolved_at": stmt.excluded.resolved_at,
        },
    )
    await db.execute(stmt)
```

- [ ] **Step 2: Update `identify_many`**

In `backend/app/services/source_identification/service_identifier.py`, add `from app.repositories.source_identification import get_cached_identities, upsert_resolved_identities`. Remove `from sqlalchemy import func, select` and `from sqlalchemy.dialects.postgresql import insert as pg_insert` once no longer used directly (verify with grep after the edit — `SourceIpIdentity` the model import may also become unused if `identify_many`'s type hints are the only remaining reference to it; keep it if so, since `dict[str, SourceIdentity]`'s *values* still get constructed from cache rows whose type comes from this import in a few places — check carefully rather than assume).

Replace the cache-read block:

```python
    cache_by_ip = await get_cached_identities(db, unique_ips)
```

(This replaces the `cache_rows = (await db.execute(select(...))).scalars().all()` and `cache_by_ip = {str(row.source_ip): row for row in cache_rows}` lines — everything from `fresh_cutoff = ...` onward is unchanged.)

Replace the upsert block — the dict-building stays exactly as it is today, only the final `await db.execute(stmt)` becomes a call to the repository function:

```python
    if resolved_now:
        now = datetime.now(timezone.utc)
        rows = [
            {
                "source_ip": ip,
                "ptr_hostname": identity.ptr_hostname,
                "service_label": identity.service_label,
                "match_method": identity.match_method.value,
                "fcrdns_valid": identity.fcrdns_valid,
                "resolved_at": now,
            }
            for ip, identity in resolved_now.items()
        ]
        await upsert_resolved_identities(db, rows)
```

- [ ] **Step 3: Run the existing tests**

Run: `pytest tests/services/source_identification/ tests/routers/test_dmarc_reports.py -v`
Expected: all pass unchanged. `identify_many` is exercised indirectly through every `test_dmarc_reports.py` test that hits `service_breakdown` (via the `_fast_ip_fallback` fixture from Plan A3), and directly by `tests/services/source_identification/test_forward_confirm.py` for its DNS-resolution half (unaffected by this task, which only touches the DB half).

- [ ] **Step 4: Add direct repository-level tests**

```python
# backend/tests/repositories/test_source_identification.py
from datetime import datetime, timezone

from app.models.enums import SourceMatchMethod
from app.models.source_ip_identity import SourceIpIdentity
from app.repositories.source_identification import get_cached_identities, upsert_resolved_identities


async def test_get_cached_identities_empty_ips_returns_empty_dict(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        result = await get_cached_identities(db, [])
    assert result == {}


async def test_upsert_then_get_round_trips(api):
    _client, owner_factory = api
    now = datetime.now(timezone.utc)
    rows = [
        {
            "source_ip": "203.0.113.10",
            "ptr_hostname": "mail.example.com",
            "service_label": "Example Mail",
            "match_method": SourceMatchMethod.ptr_domain.value,
            "fcrdns_valid": True,
            "resolved_at": now,
        }
    ]
    async with owner_factory() as db:
        await upsert_resolved_identities(db, rows)
        await db.commit()

    async with owner_factory() as db:
        result = await get_cached_identities(db, ["203.0.113.10"])

    assert "203.0.113.10" in result
    assert result["203.0.113.10"].service_label == "Example Mail"


async def test_upsert_on_conflict_updates_existing_row(api):
    _client, owner_factory = api
    now = datetime.now(timezone.utc)
    first = [
        {
            "source_ip": "203.0.113.20",
            "ptr_hostname": "old.example.com",
            "service_label": "Old Label",
            "match_method": SourceMatchMethod.ptr_domain.value,
            "fcrdns_valid": True,
            "resolved_at": now,
        }
    ]
    second = [{**first[0], "service_label": "New Label"}]
    async with owner_factory() as db:
        await upsert_resolved_identities(db, first)
        await db.commit()
    async with owner_factory() as db:
        await upsert_resolved_identities(db, second)
        await db.commit()

    async with owner_factory() as db:
        result = await get_cached_identities(db, ["203.0.113.20"])

    assert result["203.0.113.20"].service_label == "New Label"
```

Note `SourceIpIdentity` (the table) is explicitly NOT RLS-scoped (see its own model docstring) and is never truncated by the shared `api` fixture's `TRUNCATE organizations CASCADE` — if this table accumulates rows across test runs within one session in a way that causes a later test to see unexpected pre-existing data, follow the same fix pattern Plan A2's ledger used for the `platform_admins` table (a similarly non-cascaded table): scope test assertions to check for the specific expected row rather than assuming an empty table, or explicitly delete the test's own rows at the end if a genuine collision risk is found.

- [ ] **Step 5: Run the new tests plus the full suite**

Run: `pytest tests/repositories/test_source_identification.py -v` then `pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/source_identification.py backend/app/services/source_identification/service_identifier.py backend/tests/repositories/test_source_identification.py
git commit -m "Move service_identifier.py's cache read/upsert queries onto a new repository file"
```

---

### Task 5: Final verification pass

**Files:**
- Read (no modification expected): `backend/app/repositories/README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — verification only.

- [ ] **Step 1: Confirm all four service files have no leftover inline SQL**

Run: `grep -n "db.execute(\|select(\|pg_insert(" backend/app/services/rating/domain_rating.py backend/app/services/action_queue/rules.py backend/app/services/source_identification/service_identifier.py backend/app/services/dmarc_analytics.py`
Expected: no matches. If anything remains, it means a query this plan didn't account for exists — investigate before considering this plan complete rather than assuming the grep is wrong.

- [ ] **Step 2: Confirm no existing repository function was accidentally duplicated**

Run: `grep -n "^async def" backend/app/repositories/dmarc_reports.py | sort | uniq -c | awk '$1 > 1'`
Expected: no output (no function name defined twice). Repeat for `backend/app/repositories/dns_checks.py`.

- [ ] **Step 3: Confirm `app/repositories/README.md` still describes the repositories layer accurately**

Read `backend/app/repositories/README.md`. This plan added one new file (`repositories/source_identification.py`) — decide whether it's worth naming as a fourth example alongside the existing three (`domains.py`, `dmarc_reports.py`, `selectors.py`), consistent with how the file already illustrates its "one file per feature area" rule with concrete examples. Not strictly required (the rule itself doesn't need to enumerate every file), but likely a good, cheap addition — use your judgment, and say what you decided either way in your report.

- [ ] **Step 4: Confirm `app/services/README.md` still holds**

Read `backend/app/services/README.md`. No file changed subpackage-vs-flat status in this plan (`rating/`, `action_queue/`, `source_identification/` were already subpackages; `dmarc_analytics.py` stays flat) — confirm this by reading it, no edit expected.

- [ ] **Step 5: Run the full test suite one final time**

Run: `pytest -v`
Expected: every test passes, including every new repository-level test added across all four tasks.

- [ ] **Step 6: Commit if Step 3 required a doc edit**

```bash
git add backend/app/repositories/README.md
git commit -m "Note the new source_identification repository file in the repositories README"
```

Skip this commit if no doc edit was made.
