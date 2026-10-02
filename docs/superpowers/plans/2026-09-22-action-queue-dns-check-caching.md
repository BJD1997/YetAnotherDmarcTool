# Action-Queue DNS Check Caching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the two remaining live, uncached DNS lookups per domain inside `GET /action-queue` — `rua_destination_broken` and `parked_domain_not_locked_down` — by reading the DNS-check sweep's already-stored results instead of calling `check_rua_destination`/`fetch_current_dmarc_record` live on every request.

**Architecture:** No new caching mechanism. `app/services/dns_checks/dmarc.py`'s checker already runs for every domain at least every `DNS_CHECK_STALE_AFTER` (6h, via `app/services/dns_checks/scheduled_recheck.py`'s periodic sweep) and already computes both facts these two rules need — the domain's effective `p=` policy (including RFC 7489 parent inheritance) and whether `rua=` reaches the org's connected mailbox — but only as human-readable `Finding.summary` text, not structured data. This plan adds structured keys to the relevant `Finding.details` dicts, adds a small reader module that turns the latest stored `CheckType.dmarc` result into the two values the rules need, and swaps the two rule functions from live DNS calls to that reader. This is the exact same pattern `spf_lookup_limit_risk` already uses today (`latest_dns_check_results_of_type_for_domain` + reading structured `details` keys) — not a new architecture.

**Tech Stack:** No changes — same FastAPI + SQLAlchemy 2.0 async stack, same `dns_check_results` table, same `CheckType` enum.

**Context (no separate spec doc):** This plan wasn't preceded by a brainstorming/spec cycle — it was scoped directly from a live-codebase investigation (grepping actual callers, reading `dmarc.py`, `scheduled_recheck.py`, `registry.py`) during the same session that fixed the two DNS-bound N+1s in `/action-queue` and `/domains/{id}/dmarc/sender-inventory` (both fixed by batching `service_breakdown` → `service_breakdown_multi`; see `app/services/dmarc_analytics.py`). Those two fixes batched a *cached* DNS operation (`identify_many`, backed by the `source_ip_identities` table) across domains. `rua_destination_broken` and `parked_domain_not_locked_down` are a different, arguably worse problem: they have **no caching at all**, so every `/action-queue` request (which fires on every Overview page load) does 2 live DNS round-trips per domain, sequentially, for every domain in the org.

**Non-goals:** `domain_ready_for_stricter_policy`, `low_compliance_domain`, `high_volume_failure`, `spf_lookup_limit_risk` are untouched. The first three are per-domain SQL queries (not DNS-bound, not cache-less — a genuinely different kind of cost) and `spf_lookup_limit_risk` already uses the stored-results pattern this plan extends to the other two. Batching *those* SQL-bound rules is a separate, smaller-value piece of work if it's ever wanted.

## Global Constraints

- No new cache table or in-process cache — reuse `dns_check_results` (`CheckType.dmarc`) exactly as `spf_lookup_limit_risk` already does.
- **Additive-only** changes to `dmarc.py`'s `Finding.details` dicts — never remove or rename an existing key. `app/routers/dns_checks.py`'s DNS Checks page renders these `details` today (e.g. `recommendation`) and must keep working unchanged.
- Accept up to `DNS_CHECK_STALE_AFTER` (6h) staleness for both rules, same trade-off already accepted for `spf_lookup_limit_risk`. Do not add a "fall back to a live check" path inside the action-queue request — that would reintroduce the exact cost this plan removes. A domain that was never checked yet (no `dns_check_results` row — a short-lived window right after verification, since the sweep's tick is 900s, not the full 6h) must produce no action item, not an error.
- `rua_destination_broken`'s observable behavior must not change for a domain whose sweep already ran with the current mailbox connected — same "no_rua / points_elsewhere / correct" outcomes as today's live version, just read from storage instead of DNS.
- `parked_domain_not_locked_down` gets **one deliberate behavior change**, flagged in Task 4 below: it will now correctly credit an inherited parent `p=reject` (the stored check already accounts for RFC 7489 inheritance), where today's live `fetch_current_dmarc_record` call only ever looks at the domain's own record. Call this out explicitly in the PR description — don't ship it silently.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

---

### Task 1: Structured `p=` policy field on every DMARC-policy Finding

**Files:**
- Modify: `backend/app/services/dns_checks/dmarc.py`
- Test: `backend/tests/services/dns_checks/test_dmarc.py`

**Interfaces:**
- Produces: every Finding this module emits about the domain's own `p=` tag or an inherited parent policy now carries `details["p"]` — `"reject" | "quarantine" | "none" | None` (`None` = no usable policy, own or inherited, found).

**Steps:**

- [ ] In `check()`'s own-policy branch, add `"p"` to each Finding's `details` (create the dict where none exists today):

```python
    if p == "reject":
        findings.append(Finding(status="pass", summary="Policy is p=reject", details={"p": "reject"}))
    elif p == "quarantine":
        findings.append(
            Finding(
                status="warn",
                summary="Policy is p=quarantine — consider moving to p=reject once confident",
                details={
                    "p": "quarantine",
                    "recommendation": "Use the policy builder to move to p=reject once your pass rate has been stable for a while.",
                },
            )
        )
    elif p == "none":
        findings.append(
            Finding(
                status="warn",
                summary="Policy is p=none (monitoring only, no enforcement)",
                details={
                    "p": "none",
                    "recommendation": "Use the policy builder to move toward enforcement once senders are reviewed and aligned.",
                },
            )
        )
    else:
        findings.append(Finding(status="fail", summary=f"Missing or invalid p= tag: {tags.get('p')!r}", details={"p": None}))
```

- [ ] In `_check_inherited_policy()`, add the same `"p"` key to its reject/quarantine/none outcomes:

```python
    if policy == "reject":
        return [Finding(status="pass", summary=f"No record of its own — inherits {source_tag}reject from {parent_domain}", details={"p": "reject"})]
    if policy == "quarantine":
        return [
            Finding(
                status="warn",
                summary=f"No record of its own — inherits {source_tag}quarantine from {parent_domain}",
                details={
                    "p": "quarantine",
                    "recommendation": f"Consider moving {parent_domain}'s {source_tag} to reject once confident, "
                    f"or publish a stronger explicit policy at {name}.",
                },
            )
        ]
    if policy == "none":
        return [
            Finding(
                status="warn",
                summary=f"No record of its own — inherits {source_tag}none from {parent_domain} (monitoring only, no enforcement)",
                details={
                    "p": "none",
                    "recommendation": f"Use the policy builder on {parent_domain} (or publish an explicit record "
                    f"at {name}) to move toward enforcement.",
                },
            )
        ]
```

- [ ] Add `details={"p": None}` to the remaining failure-path Findings in this file: the "no parent policy either" and "couldn't check parent" branches in `_check_inherited_policy()`, the top-level "No DMARC record found" branch in `check()`, and the ">1 records found" / lookup-error branches. (Grep `Finding(status="fail"` and `Finding(status="error"` in this file — there are a handful; each one is "no usable policy," so each gets `"p": None`.)
- [ ] Add test cases to `test_dmarc.py` asserting `details["p"]` for each branch: own reject/quarantine/none/missing, inherited reject/quarantine/none, and one no-usable-policy branch (e.g. no record + no parent).
- [ ] Run `TEST_DATABASE_URL=... pytest backend/tests/services/dns_checks/test_dmarc.py -v` and confirm all pass, including existing ones (this must be additive — no existing assertion should need to change).
- [ ] Commit.

---

### Task 2: Structured `rua=` status field, including mailbox-match outcome

**Files:**
- Modify: `backend/app/services/dns_checks/dmarc.py` (same file, `rua=` block inside `check()`)
- Test: `backend/tests/services/dns_checks/test_dmarc.py`

**Interfaces:**
- Produces: the "rua= configured" Finding now carries `details = {"rua_status": "configured", "rua_targets": [...], "matches_mailbox": bool | None}` — `matches_mailbox` is `None` when `mailbox_address` wasn't known at check time (never evaluated), not merely "false." The "no rua=" Finding carries `details = {"rua_status": "no_rua"}`.

**Steps:**

- [ ] Replace the rua= block with:

```python
    rua_targets = parse_mailto_targets(tags.get("rua", ""))
    if not rua_targets:
        findings.append(
            Finding(
                status="warn",
                summary="No rua= (aggregate reporting) address configured — no visibility into DMARC results",
                details={"rua_status": "no_rua", "recommendation": "Use the policy builder to add rua= pointing at your connected mailbox."},
            )
        )
    else:
        matches_mailbox = None if not mailbox_address else mailbox_address.lower() in (t.lower() for t in rua_targets)
        findings.append(
            Finding(
                status="pass",
                summary=f"Aggregate reports (rua) configured: {', '.join(rua_targets)}",
                details={"rua_status": "configured", "rua_targets": rua_targets, "matches_mailbox": matches_mailbox},
            )
        )
        for target in rua_targets:
            ext_finding = await _check_external_destination(domain, target, "rua")
            if ext_finding:
                findings.append(ext_finding)
        if matches_mailbox is False:
            findings.append(
                Finding(
                    status="warn",
                    summary=f"rua= doesn't include your configured mailbox ({mailbox_address}) — aggregate reports won't reach it",
                    details={"recommendation": "Use the policy builder to update rua= to include your mailbox."},
                )
            )
```

  (Note this drops the redundant `points_elsewhere`-style key from the warn Finding — `matches_mailbox` on the "configured" Finding is now the single source of truth Task 3's reader uses; the warn Finding stays purely for the DNS Checks page's human-facing display, same as before.)

- [ ] Add test cases: `rua_targets` empty → `rua_status="no_rua"`; configured + `mailbox_address=None` → `matches_mailbox is None`; configured + matching `mailbox_address` → `matches_mailbox is True`; configured + non-matching `mailbox_address` → `matches_mailbox is False` and the warn Finding is also present.
- [ ] Run `TEST_DATABASE_URL=... pytest backend/tests/services/dns_checks/test_dmarc.py -v`.
- [ ] Commit.

---

### Task 3: Reader module — latest stored DMARC check as plain values

**Files:**
- Create: `backend/app/services/dns_checks/dmarc_cache.py`
- Test: `backend/tests/services/dns_checks/test_dmarc_cache.py`

**Interfaces:**
- Consumes: `latest_dns_check_results_of_type_for_domain(db, domain_id, CheckType.dmarc)` (existing, `app/repositories/dns_checks.py:61`).
- Produces: `async def latest_dmarc_policy(db, domain_id) -> str | None` and `async def latest_rua_status(db, domain_id) -> str | None` — both consumed by Task 4.

**Steps:**

- [ ] Create the module:

```python
"""Turns the DNS-check sweep's stored CheckType.dmarc findings (see
app/services/dns_checks/dmarc.py) into the plain values action_queue's
rules need, instead of those rules calling live DNS themselves. Same
"read what the periodic sweep already computed" pattern
spf_lookup_limit_risk uses for CheckType.spf — see that function in
app/services/action_queue/rules.py."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CheckType
from app.repositories.dns_checks import latest_dns_check_results_of_type_for_domain


async def latest_dmarc_policy(db: AsyncSession, domain_id: UUID) -> str | None:
    """The domain's current effective p= (own record, or inherited from a
    parent per RFC 7489), from the last sweep. None if never checked yet,
    or no usable policy was found either way."""
    rows = await latest_dns_check_results_of_type_for_domain(db, domain_id, CheckType.dmarc)
    for row in rows:
        p = (row.details or {}).get("p")
        if p is not None:
            return p
    return None


async def latest_rua_status(db: AsyncSession, domain_id: UUID) -> str | None:
    """Mirrors the old live RuaDestinationCheck.status, from the last sweep:
    'no_rua' | 'points_elsewhere' | 'correct' | 'unverified' (rua= is
    configured but the sweep ran before a mailbox_address was known — e.g.
    right after domain verification and before a mailbox was connected).
    None if never checked yet."""
    rows = await latest_dns_check_results_of_type_for_domain(db, domain_id, CheckType.dmarc)
    for row in rows:
        details = row.details or {}
        status = details.get("rua_status")
        if status == "no_rua":
            return "no_rua"
        if status == "configured":
            matches = details.get("matches_mailbox")
            if matches is True:
                return "correct"
            if matches is False:
                return "points_elsewhere"
            return "unverified"
    return None
```

- [ ] Tests: seed `DnsCheckResult` rows directly (same pattern `test_dmarc_reports.py`'s fixtures use for other models) covering: no rows at all → both return `None`; `p="reject"` row present → `latest_dmarc_policy` returns `"reject"`; `rua_status="no_rua"` → `latest_rua_status` returns `"no_rua"`; `rua_status="configured", matches_mailbox=True/False/None` → `"correct"`/`"points_elsewhere"`/`"unverified"` respectively.
- [ ] Run `TEST_DATABASE_URL=... pytest backend/tests/services/dns_checks/test_dmarc_cache.py -v`.
- [ ] Commit.

---

### Task 4: Swap the two rules from live DNS to the reader

**Files:**
- Modify: `backend/app/services/action_queue/rules.py`
- Modify: `backend/app/routers/action_queue.py` (call-site signature change)

**Interfaces:**
- Changes: `parked_domain_not_locked_down` gains a `db: AsyncSession` parameter (it needs to query now) — `async def parked_domain_not_locked_down(db: AsyncSession, domain: Domain) -> list[ActionItem]`. `rua_destination_broken`'s signature is unchanged.
- Consumes: `latest_dmarc_policy`, `latest_rua_status` from Task 3's `app.services.dns_checks.dmarc_cache`.

**Steps:**

- [ ] Replace the imports at the top of `rules.py`: remove `from app.services.dns_checks.dmarc_record import check_rua_destination, fetch_current_dmarc_record` and (if nothing else in the file uses it) `from app.services.dns_checks.resolver import DnsLookupError`; add `from app.services.dns_checks.dmarc_cache import latest_dmarc_policy, latest_rua_status`.
- [ ] Replace `rua_destination_broken`:

```python
async def rua_destination_broken(db: AsyncSession, domain: Domain, mailbox_address: str | None) -> list[ActionItem]:
    """Reads the DNS-check sweep's last stored rua= status (see
    dns_checks/dmarc_cache.py) instead of checking live DNS on every
    action-queue request. Up to DNS_CHECK_STALE_AFTER (6h) stale by design
    — same trade-off already accepted for spf_lookup_limit_risk. 'correct',
    'unverified' (sweep ran before a mailbox was connected), and 'never
    checked yet' (None) are all treated as nothing-to-flag — this rule
    only fires on a confirmed problem, never on missing data."""
    if mailbox_address is None or domain.verification_status != DomainVerificationStatus.verified:
        return []
    status = await latest_rua_status(db, domain.id)
    if status not in ("no_rua", "points_elsewhere"):
        return []
    title = (
        f"{domain.name}: no rua= address configured"
        if status == "no_rua"
        else f"{domain.name}: rua= not reaching this mailbox"
    )
    return [
        ActionItem(
            severity="serious",
            category=CATEGORY_INGESTION,
            title=title,
            action_hint="Open Policy Builder to fix rua=",
            domain_id=str(domain.id),
        )
    ]
```

- [ ] Replace `parked_domain_not_locked_down`:

```python
async def parked_domain_not_locked_down(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """Reads the DNS-check sweep's last stored policy (see
    dns_checks/dmarc_cache.py) instead of a live DMARC lookup on every
    action-queue request. Deliberate behavior change vs the old live
    version: this now correctly credits an inherited parent p=reject (the
    stored check already accounts for RFC 7489 inheritance — see dmarc.py's
    _check_inherited_policy), where the old fetch_current_dmarc_record call
    only ever looked at the domain's own record. 'Never checked yet' (None)
    is treated as nothing-to-flag, same reasoning as rua_destination_broken."""
    if domain.mail_profile == DomainMailProfile.sends_mail:
        return []
    if domain.verification_status != DomainVerificationStatus.verified:
        return []
    current_p = await latest_dmarc_policy(db, domain.id)
    if current_p is None or current_p.lower() == "reject":
        return []
    label = "receive-only" if domain.mail_profile == DomainMailProfile.receive_only else "not used for mail"
    return [
        ActionItem(
            severity="warning",
            category=CATEGORY_POLICY_READY,
            title=f"{domain.name}: marked {label} but not locked to p=reject",
            action_hint="Open Policy Builder to lock down now — no legitimate senders to protect",
            domain_id=str(domain.id),
        )
    ]
```

- [ ] In `app/routers/action_queue.py`, update the call site: `items += await parked_domain_not_locked_down(domain)` → `items += await parked_domain_not_locked_down(db, domain)`.
- [ ] `python3 -m py_compile` both changed files.
- [ ] Commit.

---

### Task 5: Test coverage proving no live DNS happens, and behavior is preserved

**Files:**
- Modify: `backend/tests/routers/test_action_queue.py`

**Interfaces:**
- Consumes: the `_add_domain` helper already added to this file (see the sender-inventory-N+1 fix earlier in this file's history); `owner_factory` for seeding `DnsCheckResult` rows directly.

**Steps:**

- [ ] Add a helper for seeding a stored DMARC check result directly (bypassing the checker/DNS entirely):

```python
from datetime import datetime, timezone

from app.models.dns_check import DnsCheckResult
from app.models.enums import CheckStatus, CheckType


async def _add_dmarc_check_result(owner_factory, org, domain, *, details: dict, status: CheckStatus = CheckStatus.pass_) -> None:
    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id,
                domain_id=domain.id,
                check_type=CheckType.dmarc,
                status=status,
                summary="seeded for test",
                details=details,
                checked_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
```

- [ ] `test_action_queue_flags_broken_rua_from_stored_check`: seed a domain (`verification_status=verified`), an org mailbox connection with a known address, and a `DnsCheckResult` with `details={"p": "reject", "rua_status": "no_rua"}`; assert `/api/action-queue` includes a "no rua= address configured" item for that domain.
- [ ] `test_action_queue_silent_when_rua_never_checked`: same seed but no `DnsCheckResult` row at all; assert no rua-related item appears (proves the `None` → silent path).
- [ ] `test_action_queue_flags_parked_domain_via_inherited_policy`: seed a `mail_profile=receive_only` domain with `details={"p": "quarantine"}` (not reject); assert the "not locked to p=reject" item appears. Then update the stored row to `details={"p": "reject"}` (simulating an inherited-reject sweep result) and assert the item disappears — this is the one directly proving Task 4's flagged behavior change actually works.
- [ ] `test_action_queue_never_calls_live_dns_for_rua_or_parked_rules`: monkeypatch `app.services.dns_checks.dmarc_record.check_rua_destination` and `fetch_current_dmarc_record` to raise `AssertionError("live DNS called")` if invoked; seed the same domains as the other tests in this task; assert `/api/action-queue` still returns 200 without raising. This is the actual regression test for the fix's whole point.
- [ ] Run `TEST_DATABASE_URL=... pytest backend/tests/routers/test_action_queue.py -v`.
- [ ] Run the full suite: `TEST_DATABASE_URL=... pytest backend -q`.
- [ ] `cd frontend && npx tsc -b` (no frontend changes expected in this plan, but this is the project's standard closing check).
- [ ] Commit.

---

## When this plan is picked back up

Follow this repo's established verification discipline: `docker cp` the changed files into `dmarc-dashboard-api-1`, `docker compose restart api` for fast iteration, then the real gate before calling it done — `docker compose build api worker` + `docker compose up -d api worker` + one more live-HTTP pass. No new alembic migration is needed (no schema change — `dns_check_results.details` is already a JSONB column, just gaining new keys).
