# Reusable Cursor Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the keyset-pagination technique that `sign_in_events` and `dmarc_reports` (by-day) already hand-roll separately into one shared backend helper and one shared frontend hook, then apply it to the three views that currently fetch their entire result set (or a hard-capped slice) in one request: TLS-RPT reports, Admin Organizations, and Admin Job Runs.

**Architecture:** Backend: a `keyset_paginate()` helper in `app/services/pagination.py`, callable by any repository function that already builds a SQLAlchemy `Select`. Frontend: a `useCursorPage()` hook wrapping `useInfiniteQuery`'s boilerplate, plus a shared `<LoadMoreButton>`. Row/card rendering stays per-view — only fetch/paging mechanics are shared.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (backend), React + TanStack Query v5 + TypeScript (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-15-reusable-pagination-design.md`

## Global Constraints

- Cursor pagination only — never `OFFSET`. Every paginated endpoint takes `limit` (`Query(50, ge=1, le=200)`, matching the existing `/sign-in-events` convention) and `before_id` (a UUID).
- Tasks 2, 3 (sign-in log, DMARC-by-day refactors) are **behavior-preserving** — same request/response shapes, same SQL semantics. Their existing tests must pass unchanged; do not edit those test files' assertions, only run them.
- Tasks 4, 5, 6 (TLS-RPT reports, Admin Organizations, Admin Job Runs) are **new pagination with an intentional response-shape change** on those specific endpoints — each one's sole frontend consumer is updated in the same plan (Tasks 10, 11, 12), so there is no orphaned caller.
- `/domains/{id}/dmarc/tls-rpt/summary` and `/domains/{id}/dmarc/tls-rpt/by-sender` are explicitly **not** touched — both need the complete result set to compute correct totals/aggregates, not one page of it. Only `/reports` (the row-by-row list) gets a paginated sibling function.
- Every new backend list/aggregate query must respect existing RLS/admin-scoping patterns already used in the file it's added to (tenant filter for org-scoped repos, `get_current_platform_admin` for admin ones) — copy the pattern already present in that file, don't invent a new one.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

---

### Task 1: Backend shared `keyset_paginate` helper

**Files:**
- Create: `backend/app/services/pagination.py`
- Test: `backend/tests/services/test_pagination.py`

**Interfaces:**
- Produces: `async def keyset_paginate(db, query, *, order_column, id_column, anchor_query, limit, scalar=True, descending=True) -> tuple[Sequence, bool]` — every later task calls this.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/services/test_pagination.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent
from app.services.pagination import keyset_paginate

from tests.conftest import seed_org_and_user


async def _seed_events(owner_factory, org_id, count):
    async with owner_factory() as db:
        for i in range(count):
            db.add(
                SignInEvent(
                    organization_id=org_id,
                    attempted_email=f"user{i}@example.com",
                    auth_method=AuthMethod.local,
                    result=SignInResult.success,
                    created_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc),
                )
            )
        await db.commit()


async def test_keyset_paginate_first_page_descending_has_more(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _seed_events(owner_factory, org.id, 3)

    async with owner_factory() as db:
        query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        rows, has_more = await keyset_paginate(
            db, query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=None, limit=2,
        )

    assert [r.attempted_email for r in rows] == ["user2@example.com", "user1@example.com"]
    assert has_more is True


async def test_keyset_paginate_second_page_exhausted(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _seed_events(owner_factory, org.id, 3)

    async with owner_factory() as db:
        first_query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        first_rows, _ = await keyset_paginate(
            db, first_query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=None, limit=2,
        )
        anchor_id = first_rows[-1].id

        second_query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        anchor_query = select(SignInEvent.created_at, SignInEvent.id).where(SignInEvent.id == anchor_id)
        rows, has_more = await keyset_paginate(
            db, second_query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=anchor_query, limit=2,
        )

    assert [r.attempted_email for r in rows] == ["user0@example.com"]
    assert has_more is False


async def test_keyset_paginate_stale_anchor_falls_back_to_first_page(api):
    """anchor_query resolving to no row (before_id points at a deleted or
    foreign row) returns the unfiltered first page rather than erroring —
    matches the existing hand-rolled behavior in list_sign_in_events."""
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _seed_events(owner_factory, org.id, 1)

    async with owner_factory() as db:
        query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        anchor_query = select(SignInEvent.created_at, SignInEvent.id).where(SignInEvent.id == uuid.uuid4())
        rows, has_more = await keyset_paginate(
            db, query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=anchor_query, limit=2,
        )

    assert len(rows) == 1
    assert has_more is False


async def test_keyset_paginate_ascending_order():
    """Admin Organizations pages alphabetically ascending, not newest-first
    — descending=False must reverse both the ORDER BY and the keyset
    comparison direction, not just the ORDER BY."""
    from app.db.session import async_session_factory
    from app.db.rls import set_platform_admin_context
    from app.models.organization import Organization

    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        for name in ["Charlie Inc", "Alpha LLC", "Bravo Co"]:
            db.add(Organization(name=name))
        await db.commit()

        query = select(Organization).where(Organization.name.in_(["Charlie Inc", "Alpha LLC", "Bravo Co"]))
        rows, has_more = await keyset_paginate(
            db, query, order_column=Organization.name, id_column=Organization.id,
            anchor_query=None, limit=2, descending=False,
        )

    assert [r.name for r in rows] == ["Alpha LLC", "Bravo Co"]
    assert has_more is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose -f docker-compose.yml exec api pytest tests/services/test_pagination.py -v` (or your local equivalent — see any earlier task's commands for the project's actual local test invocation)
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.pagination'`

- [ ] **Step 3: Write the implementation**

```python
# backend/app/services/pagination.py
"""Shared keyset (cursor) pagination for list endpoints whose result sets
can grow without bound — see
docs/superpowers/specs/2026-09-15-reusable-pagination-design.md. Offset
pagination is deliberately not used: its cost grows with how deep a query
pages, which is exactly wrong for tables built to hold years of data."""

from collections.abc import Sequence

from sqlalchemy import ColumnElement, Select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession


async def keyset_paginate(
    db: AsyncSession,
    query: Select,
    *,
    order_column: ColumnElement,
    id_column: ColumnElement,
    anchor_query: Select | None,
    limit: int,
    scalar: bool = True,
    descending: bool = True,
) -> tuple[Sequence, bool]:
    """Apply keyset pagination to `query`, already filtered/joined by the
    caller. `anchor_query` is a fully-scoped SELECT of (order_column,
    id_column) for the row identified by the caller's `before_id` — the
    caller builds it (not this function) so it can apply the same
    tenant/domain/org scoping the main query uses; pass None when there's
    no cursor yet (first page), or when `before_id` resolved to no row
    (falls back to the first page, same as the two hand-rolled call sites
    this replaces).

    `scalar=True` (default) unwraps a single-entity select (e.g.
    `select(Organization)`) via `.scalars().all()`. Pass `scalar=False` for
    a multi-column projection (e.g. a join selecting individual columns),
    which gets raw `Row` tuples via `.all()` instead — pick whichever
    matches how the caller built `query`.

    `descending=True` (default) orders and pages newest/highest-first,
    appropriate for chronological logs. Pass `descending=False` for
    ascending order (e.g. alphabetical) — this flips both the ORDER BY
    and the keyset comparison direction, not just the sort.

    Returns (rows, has_more); has_more is conservative — True whenever
    exactly `limit` rows come back, which can occasionally offer one extra
    empty "Load more" click at the true end of a result set (the same
    trade-off the two existing hand-rolled call sites already made, kept
    as-is rather than changed as a drive-by fix)."""
    if anchor_query is not None:
        anchor = (await db.execute(anchor_query)).first()
        if anchor is not None:
            comparison = tuple_(order_column, id_column) < anchor if descending else tuple_(order_column, id_column) > anchor
            query = query.where(comparison)

    order = (order_column.desc(), id_column.desc()) if descending else (order_column.asc(), id_column.asc())
    query = query.order_by(*order).limit(limit)

    result = await db.execute(query)
    rows = result.scalars().all() if scalar else result.all()
    return rows, len(rows) == limit
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `docker compose -f docker-compose.yml exec api pytest tests/services/test_pagination.py -v`
Expected: PASS, 4/4

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pagination.py backend/tests/services/test_pagination.py
git commit -m "$(cat <<'EOF'
feat: add shared keyset_paginate helper

Extracts the anchor-lookup-and-filter technique sign_in_events and
dmarc_reports each hand-roll independently into one reusable
function, for later tasks to build on.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Refactor `list_sign_in_events` onto the shared helper

**Files:**
- Modify: `backend/app/repositories/sign_in_events.py:11-42`
- Test: `backend/tests/routers/test_sign_in_events.py` (run only, no edits — this is the regression net)

**Interfaces:**
- Consumes: `keyset_paginate` from Task 1.
- Produces: no change to `list_sign_in_events`'s own signature or return type (`Sequence[SignInEvent]`) — callers (the router) are untouched.

- [ ] **Step 1: Confirm the current green baseline**

Run: `pytest tests/routers/test_sign_in_events.py -v`
Expected: PASS, all tests (this is the safety net for the refactor below — note the passing count before changing anything)

- [ ] **Step 2: Refactor to call `keyset_paginate`**

Replace `backend/app/repositories/sign_in_events.py` lines 11-42 with:

```python
from app.services.pagination import keyset_paginate


async def list_sign_in_events(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    limit: int,
    before_id: uuid.UUID | None,
    result: SignInResult | None,
    auth_method: AuthMethod | None,
) -> Sequence[SignInEvent]:
    """Keyset-paginated on (created_at, id) via keyset_paginate — see
    app/services/pagination.py. has_more is still computed by the router
    the same way it always was (len(events) == limit)."""
    query = select(SignInEvent).where(SignInEvent.organization_id == organization_id)
    if result is not None:
        query = query.where(SignInEvent.result == result)
    if auth_method is not None:
        query = query.where(SignInEvent.auth_method == auth_method)

    anchor_query = None
    if before_id is not None:
        anchor_query = select(SignInEvent.created_at, SignInEvent.id).where(
            SignInEvent.id == before_id, SignInEvent.organization_id == organization_id
        )

    rows, _has_more = await keyset_paginate(
        db, query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
        anchor_query=anchor_query, limit=limit,
    )
    return rows
```

Add the `keyset_paginate` import at the top of the file alongside the existing imports; the `tuple_` import from `sqlalchemy` is no longer used in this file and should be removed if nothing else in the file still uses it (check with `grep -n "tuple_" backend/app/repositories/sign_in_events.py` before removing).

- [ ] **Step 3: Run the tests to verify they still pass, unchanged**

Run: `pytest tests/routers/test_sign_in_events.py -v`
Expected: PASS, same count as Step 1 — including `test_list_sign_in_events_pagination`, which exercises `before_id`/`has_more` end-to-end through the router.

- [ ] **Step 4: Commit**

```bash
git add backend/app/repositories/sign_in_events.py
git commit -m "$(cat <<'EOF'
refactor: list_sign_in_events onto keyset_paginate

Behavior-preserving — same query shape, same response shape.
tests/routers/test_sign_in_events.py passes unchanged.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Refactor `list_report_records_by_day` onto the shared helper

**Files:**
- Modify: `backend/app/repositories/dmarc_reports.py:48-100`
- Test: whichever test file covers `/domains/{id}/dmarc/reports/by-day` (find it with `grep -rl "reports/by-day\|list_report_records_by_day" backend/tests`) — run only, no edits.

**Interfaces:**
- Consumes: `keyset_paginate` from Task 1.
- Produces: no change to `list_report_records_by_day`'s signature or return shape (`Sequence[Row]` — a multi-column projection, so this call uses `scalar=False`).

- [ ] **Step 1: Confirm the current green baseline**

Run: `pytest <the test file found above> -v`
Expected: PASS — note the count.

- [ ] **Step 2: Refactor to call `keyset_paginate`**

Replace `backend/app/repositories/dmarc_reports.py` lines 88-100 (the `if before_id is not None:` block through the final `return`) with:

```python
    from app.services.pagination import keyset_paginate  # add to the file's top-level imports instead

    anchor_query = None
    if before_id is not None:
        anchor_query = (
            select(DmarcAggregateReport.date_range_begin, DmarcAggregateRecord.id)
            .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
            .where(DmarcAggregateRecord.id == before_id, DmarcAggregateRecord.domain_id == domain_id)
        )

    rows, _has_more = await keyset_paginate(
        db, query, order_column=DmarcAggregateReport.date_range_begin, id_column=DmarcAggregateRecord.id,
        anchor_query=anchor_query, limit=limit, scalar=False,
    )
    return rows
```

(Move the `from app.services.pagination import keyset_paginate` line up to the file's existing import block at the top — it's written inline above only to show exactly what's being added; don't leave an import statement inside the function body.) The `tuple_` import becomes unused in this file if nothing else in it still uses it — check with `grep -n "tuple_" backend/app/repositories/dmarc_reports.py` before removing.

- [ ] **Step 3: Run the tests to verify they still pass, unchanged**

Run: `pytest <the same test file> -v`
Expected: PASS, same count as Step 1.

- [ ] **Step 4: Commit**

```bash
git add backend/app/repositories/dmarc_reports.py
git commit -m "$(cat <<'EOF'
refactor: list_report_records_by_day onto keyset_paginate

Behavior-preserving — same query shape (scalar=False, this is a
multi-column projection not a single-entity select), same response
shape. Existing by-day tests pass unchanged.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: TLS-RPT reports — paginate `/reports` only

**Files:**
- Modify: `backend/app/repositories/dns_checks.py` (add a new function; leave `list_tls_rpt_reports_for_domain` at lines 87-110 untouched)
- Modify: `backend/app/routers/dns_checks.py` (add a new helper; leave `_fetch_tls_rpt_rows` at lines 69-111 and the `/summary`, `/by-sender` endpoints untouched; change only the `/reports` endpoint at lines 145-161)
- Test: `backend/tests/routers/test_dns_checks.py`

**Interfaces:**
- Consumes: `keyset_paginate` from Task 1.
- Produces: `GET /domains/{id}/dmarc/tls-rpt/reports` now returns `{"reports": [...], "has_more": bool}` instead of a bare list — Task 10 is the one frontend consumer, updated in this same plan.

**Why not paginate the shared `_fetch_tls_rpt_rows`/`list_tls_rpt_reports_for_domain`:** both `/summary` and `/by-sender` call them and need the *complete* filtered result set to compute correct totals (`total_reports`, `total_successful_sessions`, etc.) and per-sender aggregates — paginating the shared function would silently make those two endpoints report only a partial-page's worth of totals. A new, separate paginated sibling is added instead; the existing functions are untouched.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/tests/routers/test_dns_checks.py
async def test_tls_rpt_reports_paginated(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        for i in range(3):
            db.add(
                TlsRptReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    org_name=f"sender{i}.com",
                    policy_domain=domain.name,
                    policy_type=TlsRptPolicyType.tlsa,
                    date_range_begin=now - timedelta(days=10 - i),
                    date_range_end=now - timedelta(days=9 - i),
                    summary_success_count=10,
                    summary_failure_count=0,
                    failure_details=[],
                    received_at=now - timedelta(days=9 - i),
                    created_at=now - timedelta(days=9 - i),
                )
            )
        await db.commit()
    await login_as(client, owner_factory, user)

    page1 = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports", params={"limit": 2})
    assert page1.status_code == 200
    page1_body = page1.json()
    assert len(page1_body["reports"]) == 2
    assert page1_body["has_more"] is True
    assert [r["org_name"] for r in page1_body["reports"]] == ["sender2.com", "sender1.com"]

    last_id = page1_body["reports"][-1]["id"]
    page2 = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/reports", params={"limit": 2, "before_id": last_id}
    )
    assert page2.status_code == 200
    page2_body = page2.json()
    assert [r["org_name"] for r in page2_body["reports"]] == ["sender0.com"]
    assert page2_body["has_more"] is False


async def test_tls_rpt_summary_unaffected_by_pagination(api):
    """/summary must keep seeing the complete result set, not one page —
    this is the regression check for the split described in this task."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        for i in range(3):
            db.add(
                TlsRptReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    org_name=f"sender{i}.com",
                    policy_domain=domain.name,
                    policy_type=TlsRptPolicyType.tlsa,
                    date_range_begin=now - timedelta(days=10 - i),
                    date_range_end=now - timedelta(days=9 - i),
                    summary_success_count=10,
                    summary_failure_count=0,
                    failure_details=[],
                    received_at=now - timedelta(days=9 - i),
                    created_at=now - timedelta(days=9 - i),
                )
            )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/summary")
    assert response.status_code == 200
    assert response.json()["total_reports"] == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/routers/test_dns_checks.py -k "test_tls_rpt_reports_paginated" -v`
Expected: FAIL — `/reports` still returns a bare list, no `limit`/`before_id` params recognized.

- [ ] **Step 3: Add the paginated repository function**

Add to `backend/app/repositories/dns_checks.py`, directly after `list_tls_rpt_reports_for_domain` (after line 110):

```python
async def list_tls_rpt_reports_for_domain_page(
    db: AsyncSession,
    domain_id: UUID,
    *,
    since: datetime | None,
    org_name: str | None,
    failures_only: bool,
    limit: int,
    before_id: UUID | None,
) -> tuple[Sequence[TlsRptReport], bool]:
    """Paginated sibling of list_tls_rpt_reports_for_domain, used only by
    the /reports row-by-row list. /summary and /by-sender need the
    complete result set to compute correct totals/aggregates and must
    keep calling the unpaginated function above — do not repoint them at
    this one."""
    query = select(TlsRptReport).where(TlsRptReport.domain_id == domain_id)
    if since is not None:
        query = query.where(TlsRptReport.date_range_begin >= since)
    if org_name is not None:
        query = query.where(TlsRptReport.org_name.ilike(f"%{org_name}%"))
    if failures_only:
        query = query.where(TlsRptReport.summary_failure_count > 0)

    anchor_query = None
    if before_id is not None:
        anchor_query = select(TlsRptReport.date_range_begin, TlsRptReport.id).where(
            TlsRptReport.id == before_id, TlsRptReport.domain_id == domain_id
        )

    return await keyset_paginate(
        db, query, order_column=TlsRptReport.date_range_begin, id_column=TlsRptReport.id,
        anchor_query=anchor_query, limit=limit,
    )
```

Add `from app.services.pagination import keyset_paginate` to this file's existing import block.

- [ ] **Step 4: Add the paginated router helper and update the `/reports` endpoint**

Add to `backend/app/routers/dns_checks.py`, directly after `_fetch_tls_rpt_rows` (after line 111):

```python
async def _fetch_tls_rpt_rows_page(
    db: AsyncSession,
    domain_id: uuid.UUID,
    *,
    days: int | None,
    org_name: str | None,
    result_type: str | None,
    failures_only: bool,
    limit: int,
    before_id: uuid.UUID | None,
) -> tuple[list[dict], bool]:
    """Paginated sibling of _fetch_tls_rpt_rows, for the /reports list
    only — see list_tls_rpt_reports_for_domain_page. result_type still
    filters in Python after the SQL page comes back (same reason as
    _fetch_tls_rpt_rows: failure_details is JSONB, not worth
    jsonb_array_elements at this data volume), so a page can return fewer
    than `limit` visible rows when result_type narrows it — has_more
    still reflects whether the SQL fetch itself was exhausted, so "Load
    more" stays correct even then."""
    since = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    reports, has_more = await list_tls_rpt_reports_for_domain_page(
        db, domain_id, since=since, org_name=org_name, failures_only=failures_only, limit=limit, before_id=before_id
    )

    rows = []
    for r in reports:
        failure_details = r.failure_details if isinstance(r.failure_details, list) else []
        if result_type is not None and not any(item.get("result_type") == result_type for item in failure_details):
            continue
        rows.append(
            {
                "id": str(r.id),
                "org_name": r.org_name,
                "policy_type": r.policy_type.value,
                "date_range_begin": r.date_range_begin.isoformat(),
                "date_range_end": r.date_range_end.isoformat(),
                "successful_session_count": r.summary_success_count,
                "failed_session_count": r.summary_failure_count,
                "failure_details": failure_details,
            }
        )
    return rows, has_more
```

Replace the `/reports` endpoint (lines 145-161) with:

```python
@router.get("/domains/{domain_id}/dmarc/tls-rpt/reports")
async def tls_rpt_reports(
    domain_id: uuid.UUID,
    days: int | None = Query(None, ge=1, le=3650),
    org_name: str | None = Query(None),
    result_type: str | None = Query(None),
    failures_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    before_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Row granularity is one TlsRptReport (one policy-domain within one
    report). Cursor-paginated — see
    docs/superpowers/specs/2026-09-15-reusable-pagination-design.md.
    /summary and /by-sender stay unpaginated on purpose: both need the
    complete result set to compute correct totals/aggregates, not a page
    of it."""
    await get_owned_domain(db, domain_id, user.organization_id)
    rows, has_more = await _fetch_tls_rpt_rows_page(
        db, domain_id, days=days, org_name=org_name, result_type=result_type,
        failures_only=failures_only, limit=limit, before_id=before_id,
    )
    return {"reports": rows, "has_more": has_more}
```

Add `list_tls_rpt_reports_for_domain_page` to this file's existing import of `list_latest_check_results, list_tls_rpt_reports_for_domain` from `app.repositories.dns_checks`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/routers/test_dns_checks.py -v`
Expected: PASS, including the two new tests and every pre-existing test in this file (especially `test_tls_rpt_reports_with_filters` if it asserts on the old bare-list shape — update that one assertion to read `response.json()["reports"]` instead of `response.json()` directly if it currently indexes the response as a list; find it with `grep -n "tls-rpt/reports" backend/tests/routers/test_dns_checks.py` and check each call site before running).

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/dns_checks.py backend/app/routers/dns_checks.py backend/tests/routers/test_dns_checks.py
git commit -m "$(cat <<'EOF'
feat: paginate TLS-RPT reports list endpoint

/domains/{id}/dmarc/tls-rpt/reports now returns
{reports, has_more} via keyset pagination instead of fetching every
report in one request. /summary and /by-sender are untouched — both
need the complete result set for correct totals.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Admin Organizations — pagination, search, summary

**Files:**
- Modify: `backend/app/repositories/platform_admin.py:52-54` (replace `list_all_organizations`), add `org_summary_stats`
- Modify: `backend/app/routers/platform_admin.py:305-311` (the `list_organizations` route)
- Test: `backend/tests/routers/test_platform_admin.py`

**Interfaces:**
- Consumes: `keyset_paginate` from Task 1; reuses `org_aggregates` and `_org_out` unchanged.
- Produces: `GET /admin/organizations` now returns `{"organizations": [...], "has_more": bool, "summary": {"total": int, "active": int, "suspended": int, "orgs_with_job_errors_7d": int}}` instead of a bare list, and accepts `limit`, `before_id`, `search`. Task 11 is the frontend consumer.

- [ ] **Step 1: Write the failing tests**

```python
# add to backend/tests/routers/test_platform_admin.py
async def test_list_organizations_paginated_and_searchable(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    async with owner_factory() as db:
        from app.db.rls import set_platform_admin_context
        from app.models.organization import Organization
        await set_platform_admin_context(db, is_admin=True)
        for name in ["Zeta Corp", "Alpha Corp", "Beta Corp"]:
            db.add(Organization(name=name))
        await db.commit()

    page1 = await client.get("/api/admin/organizations", params={"limit": 2})
    assert page1.status_code == 200
    body1 = page1.json()
    # Ascending alphabetical, not newest-first.
    assert [o["name"] for o in body1["organizations"]] == ["Alpha Corp", "Beta Corp"]
    assert body1["has_more"] is True
    assert body1["summary"]["total"] == 3

    last_id = body1["organizations"][-1]["id"]
    page2 = await client.get("/api/admin/organizations", params={"limit": 2, "before_id": last_id})
    body2 = page2.json()
    assert [o["name"] for o in body2["organizations"]] == ["Zeta Corp"]
    assert body2["has_more"] is False

    search = await client.get("/api/admin/organizations", params={"search": "zeta"})
    assert [o["name"] for o in search.json()["organizations"]] == ["Zeta Corp"]
    assert search.json()["summary"]["total"] == 1


async def test_organizations_summary_counts_status_and_errors(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    async with owner_factory() as db:
        from app.db.rls import set_platform_admin_context
        from app.models.organization import Organization
        from app.models.enums import OrganizationStatus
        await set_platform_admin_context(db, is_admin=True)
        db.add(Organization(name="Active One", status=OrganizationStatus.active))
        db.add(Organization(name="Suspended One", status=OrganizationStatus.suspended))
        await db.commit()

    response = await client.get("/api/admin/organizations")
    summary = response.json()["summary"]
    assert summary["active"] >= 1
    assert summary["suspended"] == 1
```

`login_as_platform_admin(client, owner_factory)` is already imported and used this way throughout this file (e.g. `test_create_and_get_organization` at line 202) — it creates a fresh admin and logs straight in, no TOTP/MFA step. Don't use `seed_platform_admin_with_totp` here — that helper is only for tests that exercise the TOTP/MFA login flow itself.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/routers/test_platform_admin.py -k "test_list_organizations_paginated_and_searchable or test_organizations_summary_counts_status_and_errors" -v`
Expected: FAIL — current endpoint ignores `limit`/`before_id`/`search` and returns a bare list with no `summary` key.

- [ ] **Step 3: Replace `list_all_organizations`, add `org_summary_stats`**

Replace `backend/app/repositories/platform_admin.py` lines 52-54 with:

```python
async def list_all_organizations(
    db: AsyncSession, *, limit: int, before_id: UUID | None, search: str | None
) -> tuple[Sequence[Organization], bool]:
    """Keyset-paginated ascending by (name, id) — alphabetical, matching
    how an admin scans for a specific org by name, rather than
    newest-first (which would bury most orgs behind whichever were most
    recently created)."""
    query = select(Organization)
    if search:
        query = query.where(Organization.name.ilike(f"%{search}%"))

    anchor_query = None
    if before_id is not None:
        anchor_query = select(Organization.name, Organization.id).where(Organization.id == before_id)

    return await keyset_paginate(
        db, query, order_column=Organization.name, id_column=Organization.id,
        anchor_query=anchor_query, limit=limit, descending=False,
    )
```

Add, directly after `org_aggregates` (after line 109):

```python
async def org_summary_stats(db: AsyncSession, *, search: str | None) -> dict:
    """Total/active/suspended counts + how many orgs have a job error in
    the last JOB_ERROR_WINDOW_DAYS days, for the Admin Organizations
    glanceable summary bar — computed once over the whole filtered set,
    not per page."""
    name_filter = Organization.name.ilike(f"%{search}%") if search else None

    status_query = select(Organization.status, func.count()).group_by(Organization.status)
    if name_filter is not None:
        status_query = status_query.where(name_filter)
    status_counts = dict((await db.execute(status_query)).all())
    total = sum(status_counts.values())
    active = status_counts.get(OrganizationStatus.active, 0)
    suspended = status_counts.get(OrganizationStatus.suspended, 0)

    error_cutoff = datetime.now(timezone.utc) - timedelta(days=JOB_ERROR_WINDOW_DAYS)
    orgs_with_errors_query = select(func.count(func.distinct(JobRun.organization_id))).where(
        JobRun.status == JobStatus.failure, JobRun.started_at >= error_cutoff,
    )
    if name_filter is not None:
        orgs_with_errors_query = orgs_with_errors_query.join(
            Organization, Organization.id == JobRun.organization_id
        ).where(name_filter)
    orgs_with_errors = (await db.execute(orgs_with_errors_query)).scalar_one()

    return {"total": total, "active": active, "suspended": suspended, "orgs_with_job_errors_7d": orgs_with_errors}
```

Add `from app.models.enums import OrganizationStatus` and `from app.services.pagination import keyset_paginate` to this file's existing import block (the `JobStatus, JobType` import already there covers `JobStatus`).

- [ ] **Step 4: Update the router endpoint**

Replace `backend/app/routers/platform_admin.py` lines 305-311 with:

```python
@router.get("/organizations")
async def list_organizations(
    limit: int = Query(50, ge=1, le=200),
    before_id: uuid.UUID | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db), _admin: AdminPrincipal = Depends(get_current_platform_admin)
) -> dict:
    orgs, has_more = await list_all_organizations(db, limit=limit, before_id=before_id, search=search)
    aggregates = await org_aggregates(db, [org.id for org in orgs])
    summary = await org_summary_stats(db, search=search)
    return {
        "organizations": [await _org_out(db, org, aggregates=aggregates.get(org.id)) for org in orgs],
        "has_more": has_more,
        "summary": summary,
    }
```

Add `org_summary_stats` to this file's existing import of `list_all_organizations, org_aggregates, ...` from `app.repositories.platform_admin`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/routers/test_platform_admin.py -v`
Expected: PASS, including the two new tests. Pre-existing tests that assert on `/admin/organizations`'s response shape as a bare list (`test_list_organizations_includes_aggregates`, `test_list_organizations_aggregates_reflect_real_data`, and any other in this file indexing the response directly as a list) must be updated to read `response.json()["organizations"]` instead — find every call site with `grep -n "get(\"/api/admin/organizations\"" backend/tests/routers/test_platform_admin.py` and check each one before running.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/platform_admin.py backend/app/routers/platform_admin.py backend/tests/routers/test_platform_admin.py
git commit -m "$(cat <<'EOF'
feat: paginate and make searchable the Admin Organizations list

GET /admin/organizations now returns
{organizations, has_more, summary} via keyset pagination
(ascending by name), plus a search param and a summary rollup
(total/active/suspended/orgs-with-job-errors) for the glanceable
summary bar.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Admin Job Runs — replace the "show last N" cap with real paging

**Files:**
- Modify: `backend/app/repositories/platform_admin.py:112-132` (`list_job_runs`)
- Modify: `backend/app/routers/platform_admin.py:494-526` (`list_job_runs_route`)
- Test: `backend/tests/routers/test_platform_admin.py`

**Interfaces:**
- Consumes: `keyset_paginate` from Task 1.
- Produces: `GET /admin/job-runs` now returns `{"job_runs": [...], "has_more": bool}` and accepts `before_id`; `limit` bounds move onto the `Query(...)` declaration itself (matching the sign-in-events convention) instead of the current manual `max(1, min(limit, 200))` clamp. Task 12 is the frontend consumer.

- [ ] **Step 1: Write the failing test**

```python
# add to backend/tests/routers/test_platform_admin.py
async def test_job_runs_paginated(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        from app.db.rls import set_platform_admin_context
        from app.models.enums import JobStatus, JobType
        from app.models.job_run import JobRun
        await set_platform_admin_context(db, is_admin=True)
        for i in range(3):
            db.add(
                JobRun(
                    organization_id=org.id, job_type=JobType.mailbox_poll, status=JobStatus.success,
                    started_at=now - timedelta(hours=3 - i), finished_at=now - timedelta(hours=3 - i) + timedelta(minutes=1),
                )
            )
        await db.commit()

    page1 = await client.get("/api/admin/job-runs", params={"limit": 2})
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["job_runs"]) == 2
    assert body1["has_more"] is True

    last_id = body1["job_runs"][-1]["id"]
    page2 = await client.get("/api/admin/job-runs", params={"limit": 2, "before_id": last_id})
    body2 = page2.json()
    assert len(body2["job_runs"]) == 1
    assert body2["has_more"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/routers/test_platform_admin.py -k test_job_runs_paginated -v`
Expected: FAIL — `before_id` unrecognized, response is currently a bare list.

- [ ] **Step 3: Refactor `list_job_runs`**

Replace `backend/app/repositories/platform_admin.py` lines 112-132 with:

```python
async def list_job_runs(
    db: AsyncSession,
    *,
    limit: int,
    before_id: UUID | None,
    organization_id: UUID | None,
    job_type: JobType | None,
    status_filter: JobStatus | None,
    since_days: int | None,
) -> tuple[Sequence[JobRun], bool]:
    query = select(JobRun)
    if organization_id is not None:
        query = query.where(JobRun.organization_id == organization_id)
    if job_type is not None:
        query = query.where(JobRun.job_type == job_type)
    if status_filter is not None:
        query = query.where(JobRun.status == status_filter)
    if since_days is not None:
        query = query.where(JobRun.started_at >= datetime.now(timezone.utc) - timedelta(days=since_days))

    anchor_query = None
    if before_id is not None:
        anchor_query = select(JobRun.started_at, JobRun.id).where(JobRun.id == before_id)

    return await keyset_paginate(
        db, query, order_column=JobRun.started_at, id_column=JobRun.id,
        anchor_query=anchor_query, limit=limit,
    )
```

- [ ] **Step 4: Update the router endpoint**

Replace `backend/app/routers/platform_admin.py` lines 494-526 with:

```python
@router.get("/job-runs")
async def list_job_runs_route(
    limit: int = Query(50, ge=1, le=200),
    before_id: uuid.UUID | None = Query(None),
    organization_id: uuid.UUID | None = Query(None),
    job_type: JobType | None = Query(None),
    status_filter: JobStatus | None = Query(None, alias="status"),
    since_days: int | None = Query(None, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _admin: AdminPrincipal = Depends(get_current_platform_admin),
) -> dict:
    runs, has_more = await list_job_runs(
        db,
        limit=limit,
        before_id=before_id,
        organization_id=organization_id,
        job_type=job_type,
        status_filter=status_filter,
        since_days=since_days,
    )
    return {
        "job_runs": [
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
        ],
        "has_more": has_more,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/routers/test_platform_admin.py -v`
Expected: PASS, including the new test. `test_job_runs_empty` (existing) must be updated from asserting a bare `[]` response to `response.json() == {"job_runs": [], "has_more": False}` — check it with `grep -n "test_job_runs_empty" -A 10 backend/tests/routers/test_platform_admin.py` before running.

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/platform_admin.py backend/app/routers/platform_admin.py backend/tests/routers/test_platform_admin.py
git commit -m "$(cat <<'EOF'
feat: paginate Admin Job Runs, replacing the "show last N" cap

GET /admin/job-runs now returns {job_runs, has_more} via keyset
pagination instead of a hard limit/offset-free row cap (previously
20/50/100/200 max, nothing beyond visible at all).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Frontend shared `useCursorPage` hook + `LoadMoreButton`

**Files:**
- Create: `frontend/src/hooks/useCursorPage.ts`
- Create: `frontend/src/components/shared/LoadMoreButton.tsx`
- Test: `frontend/src/hooks/useCursorPage.test.ts`
- Test: `frontend/src/components/shared/LoadMoreButton.test.tsx`

**Interfaces:**
- Produces: `useCursorPage<Page>(queryKey, fetchPage, getNextCursor, options?)` and `<LoadMoreButton hasNextPage isFetchingNextPage onClick />` — every later frontend task uses both.

- [ ] **Step 1: Write the failing tests**

```typescript
// frontend/src/hooks/useCursorPage.test.ts
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { makeQueryClientWrapper } from "../test/render";
import { useCursorPage } from "./useCursorPage";

interface Page { items: string[]; has_more: boolean }

describe("useCursorPage", () => {
  it("fetches the first page and exposes hasNextPage from has_more", async () => {
    const fetchPage = vi.fn(async (_cursor: string | undefined): Promise<Page> => ({
      items: ["a", "b"],
      has_more: true,
    }));
    const getNextCursor = (last: Page) => (last.has_more ? last.items[last.items.length - 1] : undefined);

    const { result } = renderHook(() => useCursorPage(["test-key"], fetchPage, getNextCursor), {
      wrapper: makeQueryClientWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchPage).toHaveBeenCalledWith(undefined);
    expect(result.current.data?.pages).toEqual([{ items: ["a", "b"], has_more: true }]);
    expect(result.current.hasNextPage).toBe(true);
  });

  it("passes the extracted cursor into fetchPage on fetchNextPage", async () => {
    const fetchPage = vi.fn(async (cursor: string | undefined): Promise<Page> =>
      cursor === undefined ? { items: ["a", "b"], has_more: true } : { items: ["c"], has_more: false },
    );
    const getNextCursor = (last: Page) => (last.has_more ? last.items[last.items.length - 1] : undefined);

    const { result } = renderHook(() => useCursorPage(["test-key-2"], fetchPage, getNextCursor), {
      wrapper: makeQueryClientWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    result.current.fetchNextPage();
    await waitFor(() => expect(result.current.data?.pages.length).toBe(2));

    expect(fetchPage).toHaveBeenLastCalledWith("b");
    expect(result.current.hasNextPage).toBe(false);
  });
});
```

```typescript
// frontend/src/components/shared/LoadMoreButton.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LoadMoreButton } from "./LoadMoreButton";

describe("LoadMoreButton", () => {
  it("renders nothing when there's no next page", () => {
    const { container } = render(<LoadMoreButton hasNextPage={false} isFetchingNextPage={false} onClick={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("calls onClick and shows a loading label while fetching", () => {
    const onClick = vi.fn();
    render(<LoadMoreButton hasNextPage={true} isFetchingNextPage={false} onClick={onClick} />);
    fireEvent.click(screen.getByText("Load more"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("disables itself and shows Loading… while isFetchingNextPage", () => {
    render(<LoadMoreButton hasNextPage={true} isFetchingNextPage={true} onClick={vi.fn()} />);
    expect(screen.getByText("Loading…")).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend run test -- useCursorPage LoadMoreButton`
Expected: FAIL — neither module exists yet.

- [ ] **Step 3: Write the implementations**

```typescript
// frontend/src/hooks/useCursorPage.ts
import { useInfiniteQuery } from "@tanstack/react-query";

/** Shared cursor-pagination wiring for list views whose result sets can
 * grow without bound — see
 * docs/superpowers/specs/2026-09-15-reusable-pagination-design.md. Each
 * resource keeps its own page response shape (e.g. {events, has_more},
 * {days, has_more}) — pass `fetchPage` to inject the cursor into that
 * resource's own URL/params, and `getNextCursor` to pull the next
 * `before_id` out of that resource's own last page (or return undefined
 * when there's no more). */
export function useCursorPage<Page>(
  queryKey: readonly unknown[],
  fetchPage: (cursor: string | undefined) => Promise<Page>,
  getNextCursor: (lastPage: Page) => string | undefined,
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: getNextCursor,
    enabled: options?.enabled,
  });
}
```

```tsx
// frontend/src/components/shared/LoadMoreButton.tsx
interface LoadMoreButtonProps {
  hasNextPage: boolean | undefined;
  isFetchingNextPage: boolean;
  onClick: () => void;
}

export function LoadMoreButton({ hasNextPage, isFetchingNextPage, onClick }: LoadMoreButtonProps) {
  if (!hasNextPage) return null;
  return (
    <button className="btn btn--secondary" style={{ marginTop: "0.75rem" }} onClick={onClick} disabled={isFetchingNextPage}>
      {isFetchingNextPage ? "Loading…" : "Load more"}
    </button>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm --prefix frontend run test -- useCursorPage LoadMoreButton`
Expected: PASS, 5/5 (2 + 3)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useCursorPage.ts frontend/src/hooks/useCursorPage.test.ts frontend/src/components/shared/LoadMoreButton.tsx frontend/src/components/shared/LoadMoreButton.test.tsx
git commit -m "$(cat <<'EOF'
feat: add shared useCursorPage hook and LoadMoreButton

Extracts the useInfiniteQuery wiring and Load-more button markup
useSignInEvents/SignInEventsSection and useDmarcReportsByDay/
DomainReports each currently duplicate, for later tasks to build on.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Refactor sign-in log onto the shared frontend pieces

**Files:**
- Modify: `frontend/src/hooks/useSignInEvents.ts:19-30`
- Modify: `frontend/src/components/settings/SignInEventsSection.tsx:78-87`
- Test: run the frontend suite (no new test file — regression-safe refactor)

**Interfaces:**
- Consumes: `useCursorPage`, `LoadMoreButton` from Task 7.
- Produces: no change to `useSignInEvents`'s external behavior — `SignInEventsSection.tsx`'s `query.data?.pages.flatMap((page) => page.events)` line needs no change, since pages keep their raw `{events, has_more}` shape.

- [ ] **Step 1: Confirm current green baseline**

Run: `npm --prefix frontend run test -- SignInEventsSection resourceHooks`
Expected: PASS

- [ ] **Step 2: Refactor `useSignInEvents.ts`**

Replace lines 19-30 with:

```typescript
import { useCursorPage } from "./useCursorPage";

export function useSignInEvents(filters: string) {
  return useCursorPage<SignInEventsPage>(
    queryKeys.signInEvents(filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<SignInEventsPage>(`/sign-in-events?${qs.toString()}`);
    },
    (lastPage) => (lastPage.has_more ? lastPage.events[lastPage.events.length - 1]?.id : undefined),
  );
}
```

Remove the now-unused `useInfiniteQuery` import from this file if nothing else in it still uses it.

- [ ] **Step 3: Swap in `LoadMoreButton`**

In `frontend/src/components/settings/SignInEventsSection.tsx`, replace lines 78-87 (the `{query.hasNextPage && (...)}` block) with:

```tsx
import { LoadMoreButton } from "../shared/LoadMoreButton";

// ...

<LoadMoreButton
  hasNextPage={query.hasNextPage}
  isFetchingNextPage={query.isFetchingNextPage}
  onClick={() => query.fetchNextPage()}
/>
```

- [ ] **Step 4: Run the tests to verify they still pass**

Run: `npm --prefix frontend run test -- SignInEventsSection resourceHooks`
Expected: PASS, unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useSignInEvents.ts frontend/src/components/settings/SignInEventsSection.tsx
git commit -m "$(cat <<'EOF'
refactor: sign-in log onto useCursorPage/LoadMoreButton

Behavior-preserving — same request shape, same page shape, same UI.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Refactor DMARC reports (by day) onto the shared frontend pieces

**Files:**
- Modify: `frontend/src/hooks/useDmarcReports.ts:11-27`
- Modify: `frontend/src/pages/domain-detail/DomainReports.tsx:202-205`
- Test: run the frontend suite (no new test file — regression-safe refactor)

**Interfaces:**
- Consumes: `useCursorPage`, `LoadMoreButton` from Task 7.
- Produces: no change to `useDmarcReportsByDay`'s external behavior.

- [ ] **Step 1: Confirm current green baseline**

Run: `npm --prefix frontend run test -- DomainReports`
Expected: PASS

- [ ] **Step 2: Refactor `useDmarcReportsByDay`**

Replace `frontend/src/hooks/useDmarcReports.ts` lines 11-27 with:

```typescript
import { useCursorPage } from "./useCursorPage";

export function useDmarcReportsByDay(domainId: string, filters: string, enabled: boolean) {
  return useCursorPage<DmarcReportsByDay>(
    queryKeys.dmarcReports.byDay(domainId, filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<DmarcReportsByDay>(`/domains/${domainId}/dmarc/reports/by-day?${qs.toString()}`);
    },
    (lastPage) => {
      if (!lastPage.has_more) return undefined;
      const lastDay = lastPage.days[lastPage.days.length - 1];
      return lastDay?.rows[lastDay.rows.length - 1]?.record_id;
    },
    { enabled },
  );
}
```

Remove the now-unused `useInfiniteQuery` import from this file if `useDmarcReportsSummary`/`useGroupedDmarcReports`/`useDmarcRecordDetail` (which stay on plain `useQuery`) don't need it.

- [ ] **Step 3: Swap in `LoadMoreButton`**

In `frontend/src/pages/domain-detail/DomainReports.tsx`, replace lines 202-205 with:

```tsx
import { LoadMoreButton } from "../../components/shared/LoadMoreButton";

// ...

<LoadMoreButton
  hasNextPage={daysQuery.hasNextPage}
  isFetchingNextPage={daysQuery.isFetchingNextPage}
  onClick={() => daysQuery.fetchNextPage()}
/>
```

(Keep the existing "Load older" label if the current button text differs from "Load more" and that distinction matters for this page — check the surrounding lines before replacing; `LoadMoreButton`'s label is fixed as "Load more"/"Loading…", so if "Load older" is intentionally different copy here, leave this one block as its own inline button rather than forcing it through the shared component, and note that in the commit message instead of silently changing user-facing copy.)

- [ ] **Step 4: Run the tests to verify they still pass**

Run: `npm --prefix frontend run test -- DomainReports`
Expected: PASS, unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useDmarcReports.ts frontend/src/pages/domain-detail/DomainReports.tsx
git commit -m "$(cat <<'EOF'
refactor: DMARC by-day reports onto useCursorPage

Behavior-preserving — same request shape, same page shape, same UI.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: TLS-RPT reports — frontend pagination

**Files:**
- Modify: `frontend/src/hooks/useTlsReports.ts:11-13` (`useTlsReportRows`)
- Modify: `frontend/src/pages/domain-detail/DomainTlsReports.tsx` (lines 40, 43, 57-122)
- Test: `frontend/src/pages/domain-detail/DomainTlsReports.test.tsx` (new)

**Interfaces:**
- Consumes: `useCursorPage`, `LoadMoreButton` from Task 7; the new `{reports, has_more}` shape from Task 4.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/domain-detail/DomainTlsReports.test.tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Outlet, Route, Routes } from "react-router-dom";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import DomainTlsReports from "./DomainTlsReports";

vi.mock("../../api/client", () => ({ api: { get: vi.fn() } }));
const getMock = vi.mocked(api.get);
beforeEach(() => getMock.mockReset());

const domain = { id: "domain-1", name: "example.com" } as const;

function renderPage() {
  return renderWithAppProviders(
    <Routes>
      <Route element={<Outlet context={domain} />}>
        <Route path="/" element={<DomainTlsReports />} />
      </Route>
    </Routes>,
  );
}

describe("DomainTlsReports", () => {
  it("shows a Load more button when the first page has more, and fetches the next page on click", async () => {
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("/summary")) return { total_reports: 3, total_successful_sessions: 30, total_failed_sessions: 0, failure_rate_pct: 0, distinct_reporting_orgs: 1, last_report_received_at: null, policy_type: null };
      if (url.includes("before_id=r1")) return { reports: [{ id: "r2", org_name: "sender.com", policy_type: "tlsa", date_range_begin: "2026-01-01T00:00:00Z", date_range_end: "2026-01-02T00:00:00Z", successful_session_count: 10, failed_session_count: 0, failure_details: [] }], has_more: false };
      return { reports: [{ id: "r1", org_name: "sender.com", policy_type: "tlsa", date_range_begin: "2026-01-02T00:00:00Z", date_range_end: "2026-01-03T00:00:00Z", successful_session_count: 20, failed_session_count: 0, failure_details: [] }], has_more: true };
    });

    renderPage();

    await waitFor(() => expect(screen.getByText("Load more")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Load more"));
    await waitFor(() => expect(screen.queryByText("Load more")).not.toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix frontend run test -- DomainTlsReports`
Expected: FAIL — `useTlsReportRows` still does a single-page fetch, no "Load more" button rendered.

- [ ] **Step 3: Refactor `useTlsReportRows`**

Replace `frontend/src/hooks/useTlsReports.ts` lines 11-13 with:

```typescript
import { useCursorPage } from "./useCursorPage";

interface TlsReportsPage { reports: TlsRptReportRow[]; has_more: boolean }

export function useTlsReportRows(domainId: string, filters: string, enabled: boolean) {
  return useCursorPage<TlsReportsPage>(
    queryKeys.tlsReports.reports(domainId, filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<TlsReportsPage>(`/domains/${domainId}/dmarc/tls-rpt/reports${qs.toString() ? `?${qs.toString()}` : ""}`);
    },
    (lastPage) => (lastPage.has_more ? lastPage.reports[lastPage.reports.length - 1]?.id : undefined),
    { enabled },
  );
}
```

- [ ] **Step 4: Wire pagination into `DomainTlsReports.tsx`**

Line 40, change:
```typescript
const reportsQuery = useTlsReportRows(domainId, filterQS, grouping === "day");
```
stays the same call, but its return type has changed shape (now an infinite query) — update line 43:
```typescript
const allRows = reportsQuery.data?.pages.flatMap((p) => p.reports) ?? [];
const days = groupByDay(allRows);
```
(replaces the old `const days = groupByDay(reportsQuery.data ?? []);`)

Add, after the closing `))}` of the `days.map((day) => ...)` block (end of the day-grouped rendering, before the `) : (` that switches to `BySenderTable`):
```tsx
<LoadMoreButton
  hasNextPage={reportsQuery.hasNextPage}
  isFetchingNextPage={reportsQuery.isFetchingNextPage}
  onClick={() => reportsQuery.fetchNextPage()}
/>
```

Add `import { LoadMoreButton } from "../../components/shared/LoadMoreButton";` to this file's imports.

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm --prefix frontend run test -- DomainTlsReports`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useTlsReports.ts frontend/src/pages/domain-detail/DomainTlsReports.tsx frontend/src/pages/domain-detail/DomainTlsReports.test.tsx
git commit -m "$(cat <<'EOF'
feat: paginate TLS-RPT reports list in the UI

DomainTlsReports' day-grouped view now loads via useCursorPage with
a Load-more button instead of fetching every report up front. The
by-sender view is unaffected (its backend endpoint still returns
the full aggregate on purpose).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Admin Organizations — search, summary bar, compact rows, pagination

**Files:**
- Modify: `frontend/src/hooks/queryKeys.ts` (the `admin.organizations` key)
- Modify: `frontend/src/hooks/useAdmin.ts:6-29, 63-68, 88-91` (types + `useAdminOrganizations` + invalidation helper)
- Modify: `frontend/src/hooks/resourceHooks.test.tsx` (remove the stale table-test row for `useAdminOrganizations`)
- Modify: `frontend/src/pages/admin/AdminOrganizations.tsx` (full rewrite of the list-rendering portion; `CreateLocalUser`, `DeleteOrgSection`, `ChangePassword` stay as-is)
- Test: `frontend/src/pages/admin/AdminOrganizations.test.tsx` (new)

**Interfaces:**
- Consumes: `useCursorPage`, `LoadMoreButton` from Task 7; the new `{organizations, has_more, summary}` shape from Task 5.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/admin/AdminOrganizations.test.tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import AdminOrganizations from "./AdminOrganizations";

vi.mock("../../api/client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../../auth/AdminAuthContext", () => ({
  useAdminAuth: () => ({ admin: { auth_type: "entra" } }),
}));
const getMock = vi.mocked(api.get);
beforeEach(() => getMock.mockReset());

function org(name: string, overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: name, name, entra_tenant_id: null, status: "active", is_operator: false, created_at: "2026-01-01T00:00:00Z",
    mailbox_connection: null, entra_consent_urls: null, domain_count: 0, job_error_count_7d: 0, last_report_at: null,
    ...overrides,
  };
}

describe("AdminOrganizations", () => {
  it("shows the glanceable summary bar and a compact row per org, collapsed by default", async () => {
    getMock.mockResolvedValueOnce({
      organizations: [org("Alpha"), org("Beta")],
      has_more: false,
      summary: { total: 2, active: 2, suspended: 0, orgs_with_job_errors_7d: 0 },
    });

    renderWithAppProviders(<AdminOrganizations />);

    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument());
    expect(screen.getByText("2")).toBeInTheDocument(); // total in summary bar
    expect(screen.queryByPlaceholderText("tenant GUID")).not.toBeInTheDocument(); // detail hidden until expanded
  });

  it("expands a row's detail on click", async () => {
    getMock.mockResolvedValueOnce({
      organizations: [org("Alpha")],
      has_more: false,
      summary: { total: 1, active: 1, suspended: 0, orgs_with_job_errors_7d: 0 },
    });

    renderWithAppProviders(<AdminOrganizations />);

    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Alpha"));
    expect(await screen.findByPlaceholderText("tenant GUID")).toBeInTheDocument();
  });

  it("restarts the query with the search term", async () => {
    getMock.mockResolvedValue({ organizations: [], has_more: false, summary: { total: 0, active: 0, suspended: 0, orgs_with_job_errors_7d: 0 } });

    renderWithAppProviders(<AdminOrganizations />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Search organizations by name"), { target: { value: "acme" } });
    await waitFor(() => expect(getMock).toHaveBeenLastCalledWith(expect.stringContaining("search=acme")));
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend run test -- AdminOrganizations`
Expected: FAIL — the current page has no search box, no summary bar, and renders every org fully expanded (no collapse/expand at all).

- [ ] **Step 3: Update `queryKeys.ts`**

In `frontend/src/hooks/queryKeys.ts`, replace the `admin.organizations` line with two entries:

```typescript
  admin: {
    currentUser: ["admin-me"] as const,
    organizationsAll: ["admin-organizations"] as const,
    organizations: (search: string) => ["admin-organizations", search] as const,
    updates: ["admin-updates"] as const,
    jobRunsSummary: ["admin-job-runs-summary"] as const,
    jobRuns: (filters: string) => ["admin-job-runs", filters] as const,
  },
```

- [ ] **Step 4: Update `useAdmin.ts`**

Replace the `AdminOrganization` interface (lines 6-29) — add a page wrapper type after it:

```typescript
export interface AdminOrganizationsPage {
  organizations: AdminOrganization[];
  has_more: boolean;
  summary: { total: number; active: number; suspended: number; orgs_with_job_errors_7d: number };
}
```

Replace `useAdminOrganizations` (lines 63-68) with:

```typescript
export function useAdminOrganizations(search: string) {
  return useCursorPage<AdminOrganizationsPage>(
    queryKeys.admin.organizations(search),
    (cursor) => {
      const qs = new URLSearchParams();
      if (search) qs.set("search", search);
      if (cursor) qs.set("before_id", cursor);
      return api.get<AdminOrganizationsPage>(`/admin/organizations${qs.toString() ? `?${qs.toString()}` : ""}`);
    },
    (lastPage) => {
      if (!lastPage.has_more) return undefined;
      const orgs = lastPage.organizations;
      return orgs[orgs.length - 1]?.id;
    },
  );
}
```

Replace `useInvalidateAdminOrganizations` (lines 88-91) with:

```typescript
function useInvalidateAdminOrganizations() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.organizationsAll });
}
```

Add `import { useCursorPage } from "./useCursorPage";` to this file's imports.

- [ ] **Step 5: Remove the stale table-test row**

In `frontend/src/hooks/resourceHooks.test.tsx`, remove the line `["admin organizations", useAdminOrganizations, "/admin/organizations", []],` from the `it.each` table (line 51) — `useAdminOrganizations` now requires a `search` argument and returns an infinite-query shape, which this generic table doesn't model. Remove the now-unused `useAdminOrganizations` import from this file's import list if nothing else in it still references it.

- [ ] **Step 6: Rewrite `AdminOrganizations.tsx`**

Replace the whole file with:

```tsx
import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Copy, Plus, Trash2, UserPlus } from "lucide-react";
import { ApiError } from "../../api/client";
import { useAdminAuth } from "../../auth/AdminAuthContext";
import { LoadMoreButton } from "../../components/shared/LoadMoreButton";
import { ReportFreshnessValue } from "../../components/overview/widgets";
import { Stat } from "../../components/domain/shared";
import {
  useAdminOrganizations, useChangeAdminPassword, useCreateAdminOrganization, useCreateAdminUser,
  useDeleteAdminOrganization, useSetAdminMailboxConnection, useUpdateAdminOrganization,
  type AdminOrganization, type AdminOrganizationsPage,
} from "../../hooks/useAdmin";
import { useClipboardFeedback } from "../../hooks/useClipboardFeedback";

const STATUS_ROLE: Record<AdminOrganization["status"], "good" | "serious"> = {
  active: "good",
  suspended: "serious",
};

export default function AdminOrganizations() {
  const { admin } = useAdminAuth();
  const [search, setSearch] = useState("");
  const query = useAdminOrganizations(search);
  const pages = query.data?.pages ?? [];
  const orgs = pages.flatMap((p) => p.organizations);
  // The summary is computed server-side over the whole filtered set on
  // every page fetch, not per page — the first page's copy is as current
  // as any other.
  const summary = pages[0]?.summary;

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const createOrg = useCreateAdminOrganization(
    () => { setName(""); setTenantId(""); setCreateError(null); },
    (err) => setCreateError(err instanceof ApiError ? err.message : "failed to create organization"),
  );

  return (
    <section>
      <div className="page-header">
        <h1>Organizations</h1>
      </div>

      {admin?.auth_type === "local" && <ChangePassword />}

      <div className="card">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createOrg.mutate({ name, entra_tenant_id: tenantId || null });
          }}
          className="field-row"
        >
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Organization name" required />
          <input
            className="input"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            placeholder="Entra tenant ID (optional)"
            style={{ width: "260px" }}
          />
          <button type="submit" className="btn btn--primary" disabled={createOrg.isPending}>
            <Plus />
            Create organization
          </button>
        </form>
        <p className="section-hint" style={{ marginBottom: 0 }}>
          Providing the tenant ID now activates the org immediately — the client can then sign in, approve consent,
          and set their own mailbox from inside their dashboard, no further steps needed here.
        </p>
        {createError && (
          <div className="alert alert--critical" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            {createError}
          </div>
        )}
      </div>

      <SummaryBar summary={summary} isLoading={query.isLoading} />

      <div className="card">
        <input
          className="input"
          placeholder="Search organizations by name"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {query.isLoading && <p className="muted">Loading…</p>}
      {query.isSuccess && orgs.length === 0 && <p className="empty-state">No organizations match your search.</p>}

      {orgs.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          {orgs.map((org) => (
            <OrgRow
              key={org.id}
              org={org}
              expanded={expandedId === org.id}
              onToggle={() => setExpandedId(expandedId === org.id ? null : org.id)}
            />
          ))}
        </div>
      )}

      <LoadMoreButton hasNextPage={query.hasNextPage} isFetchingNextPage={query.isFetchingNextPage} onClick={() => query.fetchNextPage()} />
    </section>
  );
}

function SummaryBar({ summary, isLoading }: { summary: AdminOrganizationsPage["summary"] | undefined; isLoading: boolean }) {
  if (isLoading) return <p className="muted">Loading summary…</p>;
  if (!summary) return null;
  return (
    <div className="card">
      <div className="stat-row">
        <Stat label="Organizations" value={summary.total.toLocaleString()} />
        <Stat label="Active" value={summary.active.toLocaleString()} />
        <Stat label="Suspended" value={summary.suspended.toLocaleString()} />
        <Stat label="With job errors (7d)" value={summary.orgs_with_job_errors_7d.toLocaleString()} />
      </div>
    </div>
  );
}

function OrgRow({ org, expanded, onToggle }: { org: AdminOrganization; expanded: boolean; onToggle: () => void }) {
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={onToggle}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.65rem 1.1rem", cursor: "pointer", gap: "0.75rem", flexWrap: "wrap" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <strong style={{ fontSize: "0.92rem" }}>{org.name}</strong>
          <span className={`badge badge--${STATUS_ROLE[org.status]}`}>{org.status}</span>
          {org.is_operator && <span className="badge badge--neutral">operator</span>}
        </div>
        <div className="chip-row" style={{ fontSize: "0.8rem" }}>
          <span className="muted">
            {org.domain_count} domain{org.domain_count === 1 ? "" : "s"}
          </span>
          <span className="muted">
            last report:{" "}
            {org.last_report_at ? (
              <>
                <ReportFreshnessValue hours={(Date.now() - new Date(org.last_report_at).getTime()) / 3_600_000} /> ago
              </>
            ) : (
              "never"
            )}
          </span>
          {org.job_error_count_7d > 0 && (
            <span className="badge badge--critical">
              {org.job_error_count_7d} job error{org.job_error_count_7d === 1 ? "" : "s"} (7d)
            </span>
          )}
        </div>
      </div>
      {expanded && (
        <div style={{ padding: "0 1.1rem 1rem", background: "var(--plane)", borderTop: "1px solid var(--border)" }}>
          <OrgDetail org={org} />
        </div>
      )}
    </div>
  );
}

function OrgDetail({ org }: { org: AdminOrganization }) {
  const [tenantId, setTenantId] = useState(org.entra_tenant_id ?? "");
  const [mailbox, setMailbox] = useState(org.mailbox_connection?.mailbox_address ?? "");

  const updateOrg = useUpdateAdminOrganization(org.id);
  const setMailboxConnection = useSetAdminMailboxConnection(org.id);

  const connection = org.mailbox_connection;
  const consentRole = connection
    ? connection.consent_status === "granted"
      ? "good"
      : connection.consent_status === "revoked"
        ? "critical"
        : "warning"
    : "neutral";

  return (
    <div style={{ paddingTop: "0.75rem" }}>
      <div className="field-row">
        <span className="muted" style={{ fontSize: "0.78rem" }}>Status</span>
        <select
          className="input"
          value={org.status}
          onChange={(e) => updateOrg.mutate({ status: e.target.value as AdminOrganization["status"] })}
        >
          <option value="active">active</option>
          <option value="suspended">suspended</option>
        </select>
      </div>

      <div style={{ display: "flex", gap: "3rem", marginTop: "1rem", flexWrap: "wrap" }}>
        <div>
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            Entra tenant ID
          </div>
          <div className="field-row" style={{ marginTop: "0.3rem" }}>
            <input className="input" value={tenantId} onChange={(e) => setTenantId(e.target.value)} placeholder="tenant GUID" style={{ width: "260px" }} />
            <button className="btn btn--secondary btn--sm" onClick={() => updateOrg.mutate({ entra_tenant_id: tenantId || null })}>
              Save
            </button>
          </div>
        </div>

        <div style={{ flex: 1, minWidth: 260 }}>
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            Mailbox
          </div>
          <div className="field-row" style={{ marginTop: "0.3rem" }}>
            <input className="input" value={mailbox} onChange={(e) => setMailbox(e.target.value)} placeholder="dmarc-reports@org.com" style={{ width: "220px" }} />
            <button
              className="btn btn--secondary btn--sm"
              onClick={() => setMailboxConnection.mutate({ mailbox_address: mailbox })}
              disabled={!mailbox || setMailboxConnection.isPending}
            >
              Save
            </button>
          </div>
          {connection && (
            <div style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
              <span className={`badge badge--${consentRole}`}>{connection.consent_status}</span>
              {connection.consent_status !== "granted" ? (
                <button
                  className="btn btn--ghost btn--sm"
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() => setMailboxConnection.mutate({ mailbox_address: connection.mailbox_address, consent_status: "granted" })}
                >
                  Mark granted
                </button>
              ) : (
                <button
                  className="btn btn--ghost btn--sm"
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() => setMailboxConnection.mutate({ mailbox_address: connection.mailbox_address, consent_status: "revoked" })}
                >
                  Revoke
                </button>
              )}
              <div className="muted" style={{ marginTop: "0.3rem" }}>
                {connection.last_sync_at
                  ? `Last synced ${new Date(connection.last_sync_at).toLocaleString()} (${connection.last_sync_status})`
                  : "Never synced yet"}
                {connection.last_sync_status === "error" && connection.last_sync_error && (
                  <div style={{ color: "var(--critical-text)" }}>{connection.last_sync_error}</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {!org.entra_tenant_id && !org.is_operator && <CreateLocalUser orgId={org.id} />}

      <DeleteOrgSection org={org} />
    </div>
  );
}

// CreateLocalUser, DeleteOrgSection, ChangePassword: unchanged, copy verbatim
// from the current file (lines 208-407 of the pre-rewrite AdminOrganizations.tsx).
```

Copy the existing `CreateLocalUser`, `DeleteOrgSection`, and `ChangePassword` function bodies (current file's lines 208-407) verbatim to the end of the new file — none of their internals change, only `OrgCard` is removed and replaced by `OrgRow`/`OrgDetail`/`SummaryBar` above.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `npm --prefix frontend run test -- AdminOrganizations resourceHooks`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add frontend/src/hooks/queryKeys.ts frontend/src/hooks/useAdmin.ts frontend/src/hooks/resourceHooks.test.tsx frontend/src/pages/admin/AdminOrganizations.tsx frontend/src/pages/admin/AdminOrganizations.test.tsx
git commit -m "$(cat <<'EOF'
feat: search, summary bar, and paginated compact rows for Admin Organizations

Replaces the always-expanded, unpaginated org card list with a
glanceable summary bar, a search box, and compact rows that expand
in place on click into the existing edit/delete detail — backed by
the new paginated/searchable /admin/organizations endpoint.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Admin Job Runs — replace the dropdown with real paging

**Files:**
- Modify: `frontend/src/hooks/useAdmin.ts` (`AdminJobRun`, `useAdminJobRuns`)
- Modify: `frontend/src/pages/admin/AdminJobRuns.tsx` (remove the `limit` dropdown; wire pagination)
- Test: `frontend/src/pages/admin/AdminJobRuns.test.tsx` (new)

**Interfaces:**
- Consumes: `useCursorPage`, `LoadMoreButton` from Task 7; the new `{job_runs, has_more}` shape from Task 6.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/admin/AdminJobRuns.test.tsx
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import AdminJobRuns from "./AdminJobRuns";

vi.mock("../../api/client", () => ({ api: { get: vi.fn() } }));
const getMock = vi.mocked(api.get);
beforeEach(() => getMock.mockReset());

describe("AdminJobRuns", () => {
  it("has no Show-last dropdown and shows Load more when has_more is true", async () => {
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("job-runs/summary")) return { last_failure: null, success_rate_pct_24h: null, latest_mailbox_poll_at: null, reports_processed_today: 0 };
      if (url.includes("/admin/organizations")) return { organizations: [], has_more: false, summary: { total: 0, active: 0, suspended: 0, orgs_with_job_errors_7d: 0 } };
      return {
        job_runs: [{ id: "run-1", job_type: "mailbox_poll", organization_id: null, domain_id: null, status: "success", started_at: "2026-01-01T00:00:00Z", finished_at: null, error_message: null, stats: null }],
        has_more: true,
      };
    });

    renderWithAppProviders(<AdminJobRuns />);

    await waitFor(() => expect(screen.getByText("mailbox_poll")).toBeInTheDocument());
    expect(screen.queryByText("Show last")).not.toBeInTheDocument();
    expect(screen.getByText("Load more")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm --prefix frontend run test -- AdminJobRuns`
Expected: FAIL — the "Show last" dropdown is still present, and job runs currently come back as a bare array via plain `useQuery` (no "Load more").

- [ ] **Step 3: Update `useAdmin.ts`**

Replace the `AdminJobRun` interface's usage site (`useAdminJobRuns`, lines 84-86) with:

```typescript
export interface AdminJobRunsPage { job_runs: AdminJobRun[]; has_more: boolean }

export function useAdminJobRuns(filters: string) {
  return useCursorPage<AdminJobRunsPage>(
    queryKeys.admin.jobRuns(filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<AdminJobRunsPage>(`/admin/job-runs?${qs.toString()}`);
    },
    (lastPage) => (lastPage.has_more ? lastPage.job_runs[lastPage.job_runs.length - 1]?.id : undefined),
  );
}
```

- [ ] **Step 4: Update `AdminJobRuns.tsx`**

Remove the `limit`/`setLimit` state (line 15) and the "Show last" `<select>` block (the `<label>...Show last...</label>` element around lines 118-127 per the current file). Remove `limit` from the `URLSearchParams` construction (the `new URLSearchParams({ limit: String(limit) })` line becomes `new URLSearchParams()`, still adding `organization_id`/`job_type`/`status`/`since_days` conditionally exactly as today).

Replace the destructuring `const { data: runs, isLoading, isFetching, refetch } = useAdminJobRuns(params.toString());` with:

```typescript
const query = useAdminJobRuns(params.toString());
const runs = query.data?.pages.flatMap((p) => p.job_runs) ?? [];
```

(`isLoading`/`isFetching`/`refetch` are all still available directly on `query` — update their use sites, e.g. `query.isLoading`, `query.isFetching`, `() => query.refetch()`, in place of the old destructured names throughout the rest of the file.)

After the closing `</div>` of the table's `card` wrapper (end of the `{runs && runs.length > 0 && (...)}` block), add:

```tsx
<LoadMoreButton hasNextPage={query.hasNextPage} isFetchingNextPage={query.isFetchingNextPage} onClick={() => query.fetchNextPage()} />
```

Add `import { LoadMoreButton } from "../../components/shared/LoadMoreButton";` to this file's imports.

- [ ] **Step 5: Run the test to verify it passes**

Run: `npm --prefix frontend run test -- AdminJobRuns`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useAdmin.ts frontend/src/pages/admin/AdminJobRuns.tsx frontend/src/pages/admin/AdminJobRuns.test.tsx
git commit -m "$(cat <<'EOF'
feat: paginate Admin Job Runs, remove the Show-last dropdown

Runs beyond the old hard cap (200) are now reachable via Load more
instead of being permanently invisible.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Post-Merge Deployment & Cleanup (not a subagent task)

Handled directly once this branch has been reviewed and merged — not dispatched to an implementer, since it needs a real release and prod access:

1. Cut a release from this work (same pattern as every earlier release this project — see `.github/workflows/release.yml`), same as prior `v0.1.5-beta*` cuts.
2. Upgrade the production deployment to that release (`--env-file .env`, verified image tags, backup first — same procedure used for the `v0.1.4` → `v0.1.5-beta6` upgrade).
3. Open Admin Organizations in production and verify the new search/summary/pagination against the 200 real `LOADTEST-529c40ff-*` orgs seeded on 2026-09-15 — this is the actual point of having seeded them (see `docs/superpowers/specs/2026-09-15-reusable-pagination-design.md`'s Problem section).
4. Once verified, delete them:
   ```sql
   DELETE FROM organizations WHERE name LIKE 'LOADTEST-529c40ff-%';
   ```
   via `docker compose --env-file .env exec -T db psql -U dmarc -d dmarc -c "..."` against the real prod compose file — confirm the org count drops back to its pre-seed value with a `SELECT count(*)` before/after, same as the seeding itself was verified.
5. Delete the memory file `dmarcwatch_loadtest_orgs_cleanup.md` and its `MEMORY.md` index line once step 4 is confirmed.
