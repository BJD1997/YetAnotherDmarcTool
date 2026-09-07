# DNS Checks Router — Last Inline Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the one remaining gap in `backend/app/routers/dns_checks.py`'s repository-layer migration — a single inline `TlsRptReport` query shared by three endpoints — so all 14 routers in this codebase are query-free, completing the cohesion-refactor spec's repository-layer goal for the last router that had a leftover.

**Architecture:** `dns_checks.py` is already almost entirely migrated (it imports and calls `get_owned_domain`, `list_latest_check_results`, `get_org_mailbox_connection` — all repository functions from earlier, separate refactors). Only `_fetch_tls_rpt_rows`, a private helper shared by `tls_rpt_summary`/`tls_rpt_reports`/`tls_rpt_by_sender`, still builds and executes a `select(TlsRptReport)` query inline. This plan moves just that query into `app/repositories/dns_checks.py` (the existing repository file for this router's feature area, currently home to `DnsCheckResult` queries only — this task adds a `TlsRptReport` query to it, matching the established "one file per feature area, not strictly one file per model" rule already used by `repositories/dmarc_reports.py`).

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Postgres/RLS (unchanged), pytest-asyncio + httpx `ASGITransport` HTTP-level integration tests (same pattern as every other router).

**Spec:** `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`

## Global Constraints

- The new repository function takes `db: AsyncSession` as its first argument, returns data, and holds no module-level mutable state.
- `app/repositories/dns_checks.py` already exists with `count_dns_checks_for_org` and `list_latest_check_results`, both used elsewhere (`app/routers/onboarding.py` and this same router respectively) — this task must not rename or change either function's signature, only add a new one.
- Preserve every existing inline comment when moving code — `_fetch_tls_rpt_rows`'s docstring documents a genuinely non-obvious decision (why `result_type` filters in Python instead of SQL) that must survive the move verbatim or near-verbatim.
- The Python-side filtering/shaping logic in `_fetch_tls_rpt_rows` (the `result_type` post-filter, the response-dict construction) stays in the router — only the SQL query itself moves. This mirrors Plan A3's Task 6 decision to keep `detected_domains`' Python-side assembly logic in the router rather than the repository.

---

### Task 1: Move the TLS-RPT report query into the repository layer

**Files:**
- Modify: `backend/app/repositories/dns_checks.py` (add one function)
- Modify: `backend/app/routers/dns_checks.py` (update `_fetch_tls_rpt_rows` to call it)
- Modify: `backend/tests/routers/test_dns_checks.py` (confirm existing tests still cover this path; add one new test if the existing suite doesn't already exercise the `days`/`org_name`/`failures_only` filters directly against real seeded data)

**Interfaces:**
- Consumes: nothing new from elsewhere.
- Produces: `app.repositories.dns_checks.list_tls_rpt_reports_for_domain(db: AsyncSession, domain_id: UUID, *, since: datetime | None, org_name: str | None, failures_only: bool) -> Sequence[TlsRptReport]` — consumed only by this task's router change.

- [ ] **Step 1: Read the existing test file first**

Read `backend/tests/routers/test_dns_checks.py` in full before writing any code, to see whether `tls_rpt_summary`/`tls_rpt_reports`/`tls_rpt_by_sender` already have integration tests covering the `days`/`org_name`/`failures_only`/`result_type` filter combinations with real seeded `TlsRptReport` rows. If they do, Step 5 below (new test) is unnecessary — note that in your report instead of adding a redundant test. If they don't, Step 5 is required: this task must not reduce test coverage on a function whose SQL just moved.

- [ ] **Step 2: Add the repository function**

Add to `backend/app/repositories/dns_checks.py`. New imports needed: `from datetime import datetime` (extend the existing imports), `from app.models.tls_rpt import TlsRptReport`.

```python
async def list_tls_rpt_reports_for_domain(
    db: AsyncSession,
    domain_id: UUID,
    *,
    since: datetime | None,
    org_name: str | None,
    failures_only: bool,
) -> Sequence[TlsRptReport]:
    """Shared query for all three /dmarc/tls-rpt/* endpoints in
    app/routers/dns_checks.py — result_type filtering happens in Python
    there, not here, since failure_details is a JSONB array and this
    domain's real data volume (hundreds of rows over years, not raw
    message counts) doesn't justify jsonb_array_elements. Returns
    newest-first."""
    query = select(TlsRptReport).where(TlsRptReport.domain_id == domain_id)
    if since is not None:
        query = query.where(TlsRptReport.date_range_begin >= since)
    if org_name is not None:
        query = query.where(TlsRptReport.org_name.ilike(f"%{org_name}%"))
    if failures_only:
        query = query.where(TlsRptReport.summary_failure_count > 0)
    query = query.order_by(TlsRptReport.date_range_begin.desc())
    result = await db.execute(query)
    return result.scalars().all()
```

- [ ] **Step 3: Update the router**

In `backend/app/routers/dns_checks.py`, replace the `from sqlalchemy import select` import (no longer needed once the query moves — verify nothing else in this file still uses `select` directly before removing it; if something else does, keep the import) and add `from app.repositories.dns_checks import list_latest_check_results` (already present) — extend that same import line to `from app.repositories.dns_checks import list_latest_check_results, list_tls_rpt_reports_for_domain`.

Rewrite `_fetch_tls_rpt_rows`:

```python
async def _fetch_tls_rpt_rows(
    db: AsyncSession,
    domain_id: uuid.UUID,
    *,
    days: int | None,
    org_name: str | None,
    result_type: str | None,
    failures_only: bool,
) -> list[dict]:
    """Shared filter/fetch for all three /dmarc/tls-rpt/* endpoints below —
    same _apply_report_filters-style vocabulary the DMARC reports endpoints
    use (dmarc_reports.py), applied to RFC 8460 SMTP TLS reports. days/
    org_name/failures_only filter in SQL; result_type filters in Python
    after fetch (failure_details is a JSONB array — filtering its contents
    in SQL needs jsonb_array_elements, not worth it at this domain's real
    data volume: hundreds of rows over years, not raw message counts).
    A row that qualifies on result_type still returns its full
    failure_details, not just the matching entries — the point of drilling
    into one report is seeing everything it said, not a pre-filtered slice.
    Returns newest-first."""
    since = datetime.now(timezone.utc) - timedelta(days=days) if days is not None else None
    reports = await list_tls_rpt_reports_for_domain(
        db, domain_id, since=since, org_name=org_name, failures_only=failures_only
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
    return rows
```

Note the `since` computation moved from each of the three callers into this shared helper — the original code recomputed `datetime.now(timezone.utc) - timedelta(days=days) if days else None` identically in `_fetch_tls_rpt_rows` already receiving `days` as a param and doing this exact conversion inline at its top (re-check the original file: it already did this inside `_fetch_tls_rpt_rows`, not in the three route handlers — so this is not a behavior change, just confirming the conversion stays in the same place it already was, now feeding the repository call instead of a local `query` variable).

- [ ] **Step 4: Run the existing tests**

Run: `pytest tests/routers/test_dns_checks.py -v`
Expected: all previously-passing tests in this file still PASS.

- [ ] **Step 5: Add a test only if Step 1 found a coverage gap**

If the existing test file does not already exercise `tls_rpt_reports`/`tls_rpt_summary`/`tls_rpt_by_sender` against real seeded `TlsRptReport` data with at least one filter (`days`, `org_name`, or `failures_only`), add one test that seeds 2+ `TlsRptReport` rows for a domain (varying `org_name` and `summary_failure_count`) and asserts a filtered request returns the right subset. Follow the existing seed-helper pattern already used elsewhere in this test file (or `tests/routers/test_dmarc_reports.py`'s `_add_domain`-style local helpers if this file doesn't have an equivalent yet for `TlsRptReport`).

- [ ] **Step 6: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Verify no inline SQL remains**

Run: `grep -n "select(\|db.execute(\|pg_insert(" backend/app/routers/dns_checks.py`
Expected: no matches. This is the last router with any inline query in the whole codebase — after this task, `grep -rln "select(\|db.execute(\|pg_insert(" backend/app/routers/` should show zero files with any router-level inline SQL (spot-check a couple of the already-migrated routers, e.g. `app/routers/domains.py`, to confirm this grep pattern is the right one and doesn't produce false negatives).

- [ ] **Step 8: Commit**

```bash
git add backend/app/repositories/dns_checks.py backend/app/routers/dns_checks.py backend/tests/routers/test_dns_checks.py
git commit -m "Move the last inline query (TLS-RPT reports) out of dns_checks.py onto the repository layer"
```

---

### Task 2: Final verification and docs check

**Files:**
- Read (no modification expected): `backend/app/repositories/README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — verification only.

- [ ] **Step 1: Confirm the router file has no leftover inline SQL**

Already checked in Task 1, Step 7 — re-run it here as a final gate: `grep -n "select(\|db.execute(\|pg_insert(" backend/app/routers/dns_checks.py` returns nothing.

- [ ] **Step 2: Confirm `app/repositories/README.md` still describes the repositories layer accurately**

Read `backend/app/repositories/README.md`. It should already describe `repositories/dns_checks.py` reasonably (it isn't currently named as an example in that file's text, so no wording is expected to go stale — confirm this by reading it, don't assume). No edit expected; only make one if the self-review finds something genuinely inaccurate.

- [ ] **Step 3: Run the full test suite one final time**

Run: `pytest -v`
Expected: every test passes.

- [ ] **Step 4: Commit if Step 2 required a doc edit**

```bash
git add backend/app/repositories/README.md
git commit -m "Update repositories README after the dns_checks extraction"
```

Skip this commit if no doc edit was needed.
