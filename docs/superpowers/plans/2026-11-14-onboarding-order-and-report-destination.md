# Onboarding order and report destination: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The onboarding wizard follows the real dependencies (domain → sign-in → report mailbox), report destinations are chosen by the user instead of created automatically, and an organization's own mailbox wins over a hosted address everywhere.

**Architecture:** One backend helper (`report_destination`) replaces four copies of the "hosted address or connected mailbox" rule and feeds a `ReportDestination` value into every rua= check. Hosted addresses lose their per-org gate and gain a DELETE endpoint. The frontend wizard is reordered and derives its step from real state only; small Settings and builder fixes reuse existing hooks and a new `CopyValue` component.

**Tech Stack:** FastAPI + SQLAlchemy async (Python 3.11), pytest against Postgres; React + TypeScript + react-query, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-11-14-onboarding-order-and-report-destination-design.md`

## Global Constraints

- No database changes, no migration. `organizations.hosted_mailbox_opt_in` stays in the table, unused, and leaves the API.
- Test data only: verify on the local scratch stack, never on prod.
- UI copy: never mention SAML; don't mention iCloud.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.claude/`, `qa/` or `frontend/node_modules`.
- Never run `npm install` in the worktree (`frontend/node_modules` is a symlink into the prod folder).
- Backend suite: `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/dmarc_test_wt PYTHONDONTWRITEBYTECODE=1 ~/.local/bin/pytest -q -p no:cacheprovider <path>`. Frontend: `cd frontend && npx tsc -b && npx vitest run --maxWorkers=1 <path>`. Never two heavy suites at once (2 CPUs).
- Match the surrounding code's comment density and naming.

---

### Task 1: One report destination rule, accepted by every rua= check

**Files:**
- Create: `backend/app/services/report_destination.py`
- Modify: `backend/app/services/dns_checks/dmarc_record.py:119-136`, `backend/app/services/dns_checks/tls_rpt_check.py:74-89,92,155-165`, `backend/app/services/dns_checks/dmarc.py:134,209-216,226-233`, `backend/app/services/dns_checks/starttls_tls_rpt.py:68-71`, `backend/app/services/dns_checks/registry.py:18,34,39`, `backend/app/services/dns_checks/scheduled_recheck.py:57-76`, `backend/app/routers/dns_checks.py:317-337,428-460`, `backend/app/routers/dmarc_reports.py:~695-740`
- Test: `backend/tests/services/test_report_destination.py` (create); update `backend/tests/services/dns_checks/test_tls_rpt_check.py`, `backend/tests/services/dns_checks/test_dmarc.py`

**Interfaces:**
- Produces: `ReportDestination(address: str, accepted: tuple[str, ...])` with `ReportDestination.single(address) -> ReportDestination` and `.reached_by(targets: list[str]) -> bool`; `report_destination(domain: Domain, connection: MailboxConnection | None) -> ReportDestination | None`.
- Check functions now take `destination: ReportDestination` (rua checks) or `destination: ReportDestination | None = None` (scored checks) instead of `mailbox_address`.
- Builder responses (`/domains/{id}/dmarc/policy-builder`, `/domains/{id}/dns/tls-rpt-builder`) gain `has_own_mailbox: bool`; `org_mailbox_address` now holds `destination.address`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/services/test_report_destination.py`:

```python
"""report_destination: the organization's own mailbox wins over a domain's
hosted address; either one in rua= counts as reaching this app."""

from app.models.domain import Domain
from app.models.mailbox_connection import MailboxConnection
from app.services.report_destination import ReportDestination, report_destination


def _domain(hosted: str | None) -> Domain:
    return Domain(name="example.com", hosted_report_address=hosted)


def test_own_mailbox_wins_over_hosted_address():
    dest = report_destination(_domain("reports+abc@hosted.example"), MailboxConnection(mailbox_address="dmarc@example.com"))
    assert dest is not None
    assert dest.address == "dmarc@example.com"
    assert dest.accepted == ("dmarc@example.com", "reports+abc@hosted.example")


def test_hosted_address_without_own_mailbox():
    dest = report_destination(_domain("reports+abc@hosted.example"), None)
    assert dest == ReportDestination.single("reports+abc@hosted.example")


def test_own_mailbox_without_hosted_address():
    dest = report_destination(_domain(None), MailboxConnection(mailbox_address="dmarc@example.com"))
    assert dest == ReportDestination.single("dmarc@example.com")


def test_nothing_set_up():
    assert report_destination(_domain(None), None) is None


def test_reached_by_either_accepted_address_case_insensitive():
    dest = ReportDestination(address="dmarc@example.com", accepted=("dmarc@example.com", "reports+abc@hosted.example"))
    assert dest.reached_by(["Reports+ABC@hosted.example"])
    assert dest.reached_by(["other@vendor.example", "DMARC@example.com"])
    assert not dest.reached_by(["other@vendor.example"])
```

Add to `backend/tests/services/dns_checks/test_tls_rpt_check.py`:

```python
async def test_correct_when_only_the_hosted_address_is_published():
    dest = ReportDestination(address="reports@example.com", accepted=("reports@example.com", "reports+abc@hosted.example"))
    with _fetch(_record("mailto:reports+abc@hosted.example")):
        result = await check_tls_rpt_rua_destination("example.com", dest)
    assert result.status == "correct"
```

Add to `backend/tests/services/dns_checks/test_dmarc.py` (next to the existing `mailbox_address` tests, reusing that file's DNS patch helper the neighbouring tests use):

```python
async def test_no_mismatch_finding_when_rua_points_at_the_hosted_address():
    dest = ReportDestination(address="reports@example.com", accepted=("reports@example.com", "reports+abc@hosted.example"))
    # same record-patching as test_no_mismatch_finding_when_mailbox_is_in_rua, with rua=mailto:reports+abc@hosted.example
    findings = await dmarc.check("example.com", destination=dest)
    assert not any("doesn't include your configured mailbox" in f.summary for f in findings)
```

(Copy the `with ...:` patch block from the existing passing-mailbox test directly above it and only change the rua value.)

- [ ] **Step 2: Update existing tests to the new signatures**

```bash
cd backend
sed -i 's/mailbox_address="reports@example.com"/destination=ReportDestination.single("reports@example.com")/' tests/services/dns_checks/test_dmarc.py tests/services/dns_checks/test_tls_rpt_check.py
sed -i 's/check_tls_rpt_rua_destination("example.com", "reports@example.com")/check_tls_rpt_rua_destination("example.com", ReportDestination.single("reports@example.com"))/' tests/services/dns_checks/test_tls_rpt_check.py
grep -rn 'check_rua_destination(' tests | grep -v ReportDestination
```

Add `from app.services.report_destination import ReportDestination` to both test files. Fix any call the last grep still lists the same way. Rename the "no mailbox_address given" tests' wording to "no destination given" (behaviour unchanged).

- [ ] **Step 3: Run tests to verify they fail**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/services/test_report_destination.py tests/services/dns_checks/test_tls_rpt_check.py tests/services/dns_checks/test_dmarc.py`
Expected: FAIL, `ModuleNotFoundError: app.services.report_destination`.

- [ ] **Step 4: Implement the helper**

`backend/app/services/report_destination.py`:

```python
"""Where a domain's DMARC and TLS-RPT reports go, decided in one place.

The organization's own mailbox (Microsoft 365, Google Workspace or IMAP)
wins when it has one; otherwise the domain's hosted address. Both are
processed, so a rua= with either one counts as reaching this app."""

from dataclasses import dataclass

from app.models.domain import Domain
from app.models.mailbox_connection import MailboxConnection


@dataclass(frozen=True)
class ReportDestination:
    address: str  # what the builders put in rua= and the checks name
    accepted: tuple[str, ...]  # any of these in rua= reaches this app

    @classmethod
    def single(cls, address: str) -> "ReportDestination":
        return cls(address=address, accepted=(address,))

    def reached_by(self, targets: list[str]) -> bool:
        wanted = {a.lower() for a in self.accepted}
        return any(t.lower() in wanted for t in targets)


def report_destination(domain: Domain, connection: MailboxConnection | None) -> ReportDestination | None:
    own = connection.mailbox_address if connection is not None else None
    hosted = domain.hosted_report_address
    address = own or hosted
    if address is None:
        return None
    return ReportDestination(address=address, accepted=tuple(a for a in (own, hosted) if a))
```

- [ ] **Step 5: Switch the checks to `ReportDestination`**

`dmarc_record.py`:

```python
async def check_rua_destination(domain: str, destination: ReportDestination) -> RuaDestinationCheck:
    ...  # docstring unchanged, "connected mailbox" -> "report destination"
    if destination.reached_by(targets):
        return RuaDestinationCheck(status="correct", current_targets=targets)
    return RuaDestinationCheck(status="points_elsewhere", current_targets=targets)
```

`tls_rpt_check.py`: same change in `check_tls_rpt_rua_destination(domain: str, destination: ReportDestination)`; `check(domain: str, destination: ReportDestination | None = None)`, and its mismatch block becomes:

```python
    if destination and valid:
        mailto_targets = _parse_mailto_targets(tags.get("rua", ""))
        if not destination.reached_by(mailto_targets):
            findings.append(
                Finding(
                    status="warn",
                    summary=f"TLS-RPT rua= doesn't include your configured mailbox ({destination.address}) — reports won't reach it",
                    details={"recommendation": "Use the TLS-RPT record builder to update rua= to include your mailbox."},
                )
            )
```

`dmarc.py`: `check(domain: str, parent_domain: str | None = None, destination: ReportDestination | None = None)`; the rua block uses `if destination and not destination.reached_by(rua_targets):` with `{destination.address}` in the summary; the ruf block uses `if ruf_targets and destination and not destination.reached_by(ruf_targets):` likewise. Update the module docstring's "mailbox_address" mention.

`starttls_tls_rpt.py`:

```python
async def check(db: AsyncSession, domain_id: uuid.UUID, domain_name: str, destination: ReportDestination | None) -> list[Finding]:
    if destination is None:
        return []
    rua_status = (await check_tls_rpt_rua_destination(domain_name, destination)).status
```

`registry.py`: `run_all(..., destination: ReportDestination | None = None)`, passing `destination` to `dmarc.check(domain_name, parent_domain_name, destination)` and `tls_rpt_check.check(domain_name, destination)`.

Import `ReportDestination` from `app.services.report_destination` in each module.

- [ ] **Step 6: Use the helper in the four callers**

`scheduled_recheck.py` (replace the comment + line at 57-63):

```python
    # One rule for every place that needs it: see app/services/report_destination.py.
    connection = await get_org_mailbox_connection(db, domain.organization_id)
    destination = report_destination(domain, connection)
```
and pass `destination` to `run_all(...)` and `starttls_tls_rpt.check(db, domain.id, domain.name, destination)`.

`routers/dns_checks.py` `dmarc_rua_check`:

```python
    domain = await get_owned_domain(db, domain_id, user.organization_id)
    connection = await get_org_mailbox_connection(db, user.organization_id)
    destination = report_destination(domain, connection)
    if destination is None:
        return {"status": "no_mailbox", "current_targets": [], "org_mailbox_address": None}

    result = await check_rua_destination(domain.name, destination)
    return {
        "status": result.status,
        "current_targets": result.current_targets,
        "org_mailbox_address": destination.address,
    }
```
Update its docstring: "the org's connected mailbox if it has one, else the domain's own hosted address".

`routers/dns_checks.py` `tls_rpt_builder` and `routers/dmarc_reports.py` (DMARC builder): replace the `mailbox_address = ...` line with `destination = report_destination(domain, connection)`, call the rua check with `destination` when it isn't `None`, and return:

```python
        "org_mailbox_address": destination.address if destination else None,
        "hosted_report_address": domain.hosted_report_address,
        "has_own_mailbox": connection is not None,
```
Replace the "prefer it over the org's shared MailboxConnection" comment in `dmarc_reports.py` with a pointer to `report_destination`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/services tests/routers/test_dns_checks.py tests/routers/test_dmarc_reports.py tests/routers/test_cross_org_isolation.py tests/routers/test_review_round_2.py`
Expected: PASS. If `test_dns_checks.py`'s monkeypatched `_rua_correct` breaks, make its signature `(domain, destination)`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/report_destination.py backend/app/services/dns_checks backend/app/routers/dns_checks.py backend/app/routers/dmarc_reports.py backend/tests
git commit -m "Own mailbox wins over a hosted address; rua= with either counts as reaching YADT"
```

---

### Task 2: Hosted address for every organization, and removable

**Files:**
- Modify: `backend/app/routers/domains.py:300-350`, `backend/app/routers/organizations.py:31,80-81`, `backend/app/schemas/organizations.py:13`, `backend/app/models/organization.py:84-87` (comment only)
- Test: `backend/tests/routers/test_review_round_2.py`, `backend/tests/routers/test_organizations.py`

**Interfaces:**
- Produces: `DELETE /api/domains/{domain_id}/hosted-report-address` → 204; 409 `"connect your own mailbox before removing the hosted address"` without a mailbox connection; 404 for another org's domain (via `get_owned_domain`).
- `POST /api/domains/{id}/hosted-report-address` no longer returns 403 for any org.
- `GET /api/organizations/current` no longer has `hosted_mailbox_opt_in`; PATCH ignores it.

- [ ] **Step 1: Write the failing tests** (append to `test_review_round_2.py`, which has `_seed_domain` and `dns_provisioner` imported)

```python
async def test_hosted_address_allowed_for_a_microsoft_org_without_opt_in(api, monkeypatch):
    from app.config import settings

    client, owner_factory = api
    monkeypatch.setattr(settings, "hosted_reports_domain", "hosted.example")
    monkeypatch.setattr(settings, "hosted_reports_mailbox_address", "reports@hosted.example")

    async def _ensure(name):
        return dns_provisioner.ProvisionResult(status="unconfigured", detail=None)

    monkeypatch.setattr("app.routers.domains.ensure_authorization_record", _ensure)
    org, admin = await seed_org_and_user(owner_factory, entra=True)
    domain = await _seed_domain(owner_factory, org, "ms-org.example")
    await login_as(client, owner_factory, admin)

    response = await client.post(f"/api/domains/{domain.id}/hosted-report-address")
    assert response.status_code == 200
    assert response.json()["hosted_report_address"].startswith("reports+")


async def test_removing_a_hosted_address_needs_an_own_mailbox(api, monkeypatch):
    released: list[set[str]] = []

    async def _release(names):
        released.append(names)

    monkeypatch.setattr("app.routers.domains.release_authorization_records", _release)
    client, owner_factory = api
    org, admin = await seed_org_and_user(owner_factory)
    domain = await _seed_domain(owner_factory, org, "remove-me.example", hosted=True)
    await login_as(client, owner_factory, admin)

    refused = await client.delete(f"/api/domains/{domain.id}/hosted-report-address")
    assert refused.status_code == 409
    assert released == []

    async with owner_factory() as db:
        db.add(MailboxConnection(organization_id=org.id, mailbox_address="dmarc@remove-me.example"))
        await db.commit()

    assert (await client.delete(f"/api/domains/{domain.id}/hosted-report-address")).status_code == 204
    assert released == [{"remove-me.example"}]
    async with owner_factory() as db:
        assert (await db.get(Domain, domain.id)).hosted_report_address is None


async def test_removing_a_hosted_address_of_another_org_is_404(api):
    client, owner_factory = api
    other_org, _ = await seed_org_and_user(owner_factory)
    foreign = await _seed_domain(owner_factory, other_org, "foreign.example", hosted=True)
    _org, admin = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, admin)

    assert (await client.delete(f"/api/domains/{foreign.id}/hosted-report-address")).status_code == 404
```

Add `from app.models.mailbox_connection import MailboxConnection` if the file lacks it. In `test_organizations.py`, add:

```python
async def test_current_organization_no_longer_exposes_hosted_opt_in(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=True)
    await login_as(client, owner_factory, user)

    assert "hosted_mailbox_opt_in" not in (await client.get("/api/organizations/current")).json()
```

Delete any existing test asserting the 403 for an Entra org without opt-in or the opt-in PATCH round-trip (`grep -rn "hosted_mailbox_opt_in\|isn't enabled for your organization" backend/tests`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_review_round_2.py tests/routers/test_organizations.py`
Expected: FAIL (403 for the Microsoft org; 405 for DELETE; key still present).

- [ ] **Step 3: Implement**

In `routers/domains.py`: delete `_hosted_mailbox_available` and the `if not _hosted_mailbox_available(org): raise ...403` block (and the now-unused `org = await get_organization(...)` line if nothing else uses it). Update the POST docstring: "for any organization, next to or instead of its own mailbox". Add below it:

```python
@router.delete("/{domain_id}/hosted-report-address", status_code=status.HTTP_204_NO_CONTENT)
async def remove_hosted_report_address(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> None:
    """Only while the organization has its own mailbox, so a domain is never
    left without anywhere for its reports to go. Reports already received
    stay; later mail to the old address is skipped by the hosted poll."""
    domain = await get_owned_domain(db, domain_id, user.organization_id)
    if await get_org_mailbox_connection(db, user.organization_id) is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "connect your own mailbox before removing the hosted address")
    if domain.hosted_report_address is None:
        return
    domain.hosted_report_address = None
    await db.commit()
    await release_authorization_records({domain.name})
```
(Import `get_org_mailbox_connection` from `app.repositories.mailbox_connections` if not already imported.)

In `routers/organizations.py` remove the `"hosted_mailbox_opt_in"` key and the PATCH branch; in `schemas/organizations.py` remove the field. In `models/organization.py` change the column's comment to: `# Unused since v0.3.0-beta21 (hosted addresses are open to every organization); dropped in v0.4.0.`

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_review_round_2.py tests/routers/test_organizations.py tests/routers/test_domains.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/domains.py backend/app/routers/organizations.py backend/app/schemas/organizations.py backend/app/models/organization.py backend/tests
git commit -m "Hosted addresses for every organization; remove one once an own mailbox is connected"
```

---

### Task 3: Onboarding status from real state

**Files:**
- Modify: `backend/app/routers/onboarding.py:25-58`
- Test: `backend/tests/routers/test_onboarding.py`

**Interfaces:**
- Produces in `GET /api/onboarding/status`: `has_mailbox` = mailbox connection exists OR any domain of the org has a hosted address; new `has_sign_in_provider: bool` (`org.sign_in_provider != "none"`).

- [ ] **Step 1: Write the failing tests** (replace the two `fresh_org_*` tests)

```python
async def test_onboarding_status_fresh_org_has_no_mailbox_whatever_its_sign_in(api):
    client, owner_factory = api
    for entra in (False, True):
        _org, user = await seed_org_and_user(owner_factory, entra=entra)
        await login_as(client, owner_factory, user)
        body = (await client.get("/api/onboarding/status")).json()
        assert body["has_mailbox"] is False
        assert body["has_sign_in_provider"] is entra


async def test_onboarding_status_hosted_address_counts_as_mailbox(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(
            organization_id=org.id, name="hosted-only.example",
            verification_status=DomainVerificationStatus.verified,
            hosted_report_address="reports+abc123@hosted.example",
        ))
        await db.commit()
    await login_as(client, owner_factory, user)

    assert (await client.get("/api/onboarding/status")).json()["has_mailbox"] is True


async def test_onboarding_status_own_mailbox_counts_as_mailbox(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(MailboxConnection(organization_id=org.id, mailbox_address="dmarc@example.com"))
        await db.commit()
    await login_as(client, owner_factory, user)

    assert (await client.get("/api/onboarding/status")).json()["has_mailbox"] is True
```

Import `MailboxConnection` from `app.models.mailbox_connection`.

- [ ] **Step 2: Run to verify failure**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_onboarding.py`
Expected: FAIL (`has_mailbox` True for a local-auth org; `has_sign_in_provider` missing).

- [ ] **Step 3: Implement**

Replace the comment + `has_mailbox = ...` line with:

```python
    # Any report destination counts: the organization's own mailbox or a
    # hosted address on one of its domains. Never implied by how the
    # organization signs in.
    has_mailbox = connection is not None or await count_hosted_domains_for_org(db, user.organization_id) > 0
```

and add `"has_sign_in_provider": org is not None and org.sign_in_provider != "none",` to the response. Add to `backend/app/repositories/domains.py`, next to `count_verified_domains_for_org` and in its style:

```python
async def count_hosted_domains_for_org(db: AsyncSession, organization_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Domain).where(
            Domain.organization_id == organization_id, Domain.hosted_report_address.is_not(None)
        )
    )
    return result.scalar_one()
```

- [ ] **Step 4: Run to verify pass**

Run: `~/.local/bin/pytest -q -p no:cacheprovider tests/routers/test_onboarding.py tests/repositories`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/onboarding.py backend/app/repositories/domains.py backend/tests/routers/test_onboarding.py
git commit -m "Onboarding status: report mailbox and sign-in from what's actually set up"
```

---

### Task 4: Copy buttons and Settings fixes

**Files:**
- Create: `frontend/src/components/CopyValue.tsx`, `frontend/src/components/settings/PendingDomains.tsx`
- Modify: `frontend/src/pages/Domains.tsx:390-396`, `frontend/src/pages/settings/DomainsTab.tsx:14-23`, `frontend/src/pages/settings/GeneralTab.tsx:62,245-292`
- Test: `frontend/src/pages/settings/DomainsTab.test.tsx`, `frontend/src/pages/settings/GeneralTab.test.tsx`

**Interfaces:**
- Produces: `<CopyValue label?: string value: string />` (used again in Task 6); `<PendingDomains />` (lists apex domains with `verification_status !== "verified"`, each with host name, TXT value and "Verify now"; used again in Task 6).

- [ ] **Step 1: Write the failing tests**

`DomainsTab.test.tsx` (follow the file's existing `api.get` mock):

```tsx
it("lists a pending domain with its verification record and copy buttons", async () => {
  getMock.mockImplementation(async (url: string) => {
    if (url === "/domains")
      return [{ id: "d1", name: "gtest.example", parent_domain_id: null, created_at: "2026-11-14T00:00:00Z",
        verification_status: "pending", verification_record_name: "_yadt-verify.gtest.example", verification_token: "yadt-verify=abc" }];
    return [];
  });
  renderWithAppProviders(<DomainsTab />);

  expect(await screen.findByText("_yadt-verify.gtest.example")).toBeInTheDocument();
  expect(screen.getByText("yadt-verify=abc")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /Copy/ })).toHaveLength(2);
  expect(screen.getByRole("button", { name: /Verify now/ })).toBeInTheDocument();
});
```

`GeneralTab.test.tsx`:

```tsx
it("asks to verify a domain before showing the mailbox forms", async () => {
  // existing org/mailbox mocks, with /domains returning []
  renderWithAppProviders(<GeneralTab />);
  expect(await screen.findByText(/Verify a domain first/)).toBeInTheDocument();
  expect(screen.queryByPlaceholderText("imap.example.com")).not.toBeInTheDocument();
});

it("has no hosted mailbox on/off switch", async () => {
  renderWithAppProviders(<GeneralTab />);
  expect(await screen.findByText(/as well as or instead of your own mailbox/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "On" })).not.toBeInTheDocument();
});
```

Remove existing GeneralTab tests that click the hosted On/Off switch.

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run --maxWorkers=1 src/pages/settings`
Expected: FAIL

- [ ] **Step 3: Implement**

`frontend/src/components/CopyValue.tsx`:

```tsx
import { Check, Copy } from "lucide-react";
import { useClipboardFeedback } from "../hooks/useClipboardFeedback";

// A DNS value people paste into their DNS provider, with a copy button.
export default function CopyValue({ label, value }: { label?: string; value: string }) {
  const { copied, copy } = useClipboardFeedback();
  return (
    <span className="chip-row" style={{ gap: "0.35rem", alignItems: "center" }}>
      {label && <span className="muted">{label}</span>}
      <code style={{ wordBreak: "break-all" }}>{value}</code>
      <button type="button" className="icon-btn" onClick={() => void copy(value)} aria-label={`Copy ${label ?? value}`}>
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </span>
  );
}
```

`frontend/src/components/settings/PendingDomains.tsx`:

```tsx
import { Link } from "react-router-dom";
import type { Domain } from "../../api/types";
import { useDomains, useVerifyDomain } from "../../hooks/useDomains";
import CopyValue from "../CopyValue";

// Domains still waiting for their verification TXT record, with what to
// publish. Polls while any is pending so a background verification shows up.
export default function PendingDomains() {
  const { data: domains } = useDomains({
    refetchInterval: (query) =>
      (query.state.data ?? []).some((d) => !d.parent_domain_id && d.verification_status === "pending") ? 10000 : false,
  });
  const pending = (domains ?? []).filter((d) => !d.parent_domain_id && d.verification_status !== "verified");
  if (pending.length === 0) return null;
  return (
    <div style={{ display: "grid", gap: "0.75rem", marginTop: "1rem" }}>
      {pending.map((d) => <PendingDomain key={d.id} domain={d} />)}
      <p className="section-hint" style={{ margin: 0 }}>
        All domains: <Link to="/domains">Domains page</Link>.
      </p>
    </div>
  );
}

function PendingDomain({ domain }: { domain: Domain }) {
  const verify = useVerifyDomain(domain.id);
  return (
    <div className="card" style={{ padding: "0.9rem" }}>
      <strong>{domain.name}</strong> — add this TXT record, then verify:
      <div style={{ display: "grid", gap: "0.3rem", margin: "0.5rem 0" }}>
        <CopyValue label="Host/name" value={domain.verification_record_name} />
        <CopyValue label="Value" value={domain.verification_token} />
      </div>
      <button className="btn btn--secondary btn--sm" onClick={() => verify.mutate()} disabled={verify.isPending}>
        {verify.isPending ? "Checking…" : "Verify now"}
      </button>
      {verify.isSuccess && !verify.data.verified && (
        <p className="section-hint" style={{ color: "var(--critical-text)", marginBottom: 0 }}>
          Not found yet — DNS changes can take a few minutes to propagate.
        </p>
      )}
    </div>
  );
}
```

(If `Domain` types `verification_record_name`/`verification_token` as optional, use `?? ""`.)

`DomainsTab.tsx`: render `<PendingDomains />` right after `<AddDomainForm />`.

`Domains.tsx:393-395`: replace the two `<strong>` lines with `<CopyValue label="Host/name" value={domain.verification_record_name} />` and `<CopyValue label="TXT value" value={domain.verification_token} />` (each on its own line).

`GeneralTab.tsx:62`: wrap the mailbox section:

```tsx
      {hasVerifiedDomain ? (
        <MailboxConnectionSection canManage={canManage} />
      ) : (
        <div className="alert alert--warning">
          Verify a domain first: the report mailbox has to be on one of your verified domains.{" "}
          <Link to="/settings/domains">Settings → Domains</Link>
        </div>
      )}
```
with `const { data: domains } = useDomains(); const hasVerifiedDomain = (domains ?? []).some((d) => d.verification_status === "verified");` — but keep showing the section when a connection already exists (`|| !!connection` via `useMailboxConnection()`), so an existing mailbox never disappears. Check the Settings route path with `grep -rn 'settings/' frontend/src/App.tsx` and use the real one.

`HostedMailboxSection`: drop the switch and the `entra_tenant_id` branch; render for every org:

```tsx
function HostedMailboxSection() {
  const { data: status } = useOnboardingStatus();
  return (
    <div className="card" style={{ padding: "1rem" }}>
      <p className="section-hint" style={{ margin: 0 }}>
        {status && !status.hosted_mailbox_ready
          ? "This server doesn't offer hosted reporting addresses."
          : "Available on this server: each domain can get a YetAnotherDmarcTool-hosted reporting address, as well as or instead of your own mailbox above. Add one from a domain's DMARC policy builder."}
      </p>
    </div>
  );
}
```

Remove `hosted_mailbox_opt_in` from `api/types.ts` and from the `useUpdateOrganization` Pick in `hooks/useOrganization.ts`.

- [ ] **Step 4: Run to verify pass**

Run: `npx tsc -b && npx vitest run --maxWorkers=1 src/pages`
Expected: PASS (tsc will point at every remaining `hosted_mailbox_opt_in` use; Task 5 removes the builder ones, so fix them here only if tsc blocks, by reading `false`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CopyValue.tsx frontend/src/components/settings/PendingDomains.tsx frontend/src/pages frontend/src/api/types.ts frontend/src/hooks/useOrganization.ts
git commit -m "Copy buttons for verification records; pending domains and verify-first hint in Settings"
```

---

### Task 5: Policy builders and Overview badge

**Files:**
- Modify: `frontend/src/components/policy-builder/PolicyBuilder.tsx:56,108-116,129-210`, `frontend/src/components/policy-builder/TlsRptPolicyBuilder.tsx:33,100-150` (same shape), `frontend/src/hooks/usePolicyBuilders.ts`, `frontend/src/api/policyBuilder.ts` (response types), `frontend/src/components/overview/widgets.tsx:66-78`
- Test: `frontend/src/components/policy-builder/PolicyBuilder.test.tsx`

**Interfaces:**
- Consumes: builder responses' `org_mailbox_address`, `hosted_report_address`, `has_own_mailbox` (Task 1); `DELETE /domains/{id}/hosted-report-address` (Task 2); `OnboardingStatus.hosted_mailbox_ready`.
- Produces: `useRemoveHostedReportAddress(domainId, onSuccess?, onError?)` in `usePolicyBuilders.ts`.

- [ ] **Step 1: Write the failing tests** (`PolicyBuilder.test.tsx`, using its existing builder-response factory)

```tsx
it("shows the own mailbox as destination and the hosted address once, unticked, with a remove button", async () => {
  builder({ org_mailbox_address: "reports@gtest.example", hosted_report_address: "reports+421da980d918@hosted.example",
    has_own_mailbox: true, rua_destination: { status: "not_configured", current_targets: [] } });
  renderWithAppProviders(<PolicyBuilder domainId="d1" domainName="gtest.example" onClose={() => {}} />);

  expect(await screen.findByText("reports@gtest.example")).toBeInTheDocument();
  expect(screen.getAllByText("reports+421da980d918@hosted.example")).toHaveLength(1);
  expect(screen.getByRole("checkbox", { name: /reports\+421da980d918/ })).not.toBeChecked();
  expect(screen.getByRole("button", { name: /Remove hosted address/ })).toBeInTheDocument();
  expect(screen.getByText(/rua=mailto:reports@gtest.example(?!,)/)).toBeInTheDocument();
});

it("shows a hosted-only destination once and offers no remove button", async () => {
  builder({ org_mailbox_address: "reports+abc@hosted.example", hosted_report_address: "reports+abc@hosted.example",
    has_own_mailbox: false, rua_destination: { status: "not_configured", current_targets: [] } });
  renderWithAppProviders(<PolicyBuilder domainId="d1" domainName="example.com" onClose={() => {}} />);

  expect(await screen.findAllByText("reports+abc@hosted.example")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: /Remove hosted address/ })).not.toBeInTheDocument();
});

it("offers a hosted address to an organization that signs in with Microsoft", async () => {
  org({ entra_tenant_id: "00000000-0000-0000-0000-000000000001" });
  builder({ org_mailbox_address: "dmarc@example.com", hosted_report_address: null, has_own_mailbox: true });
  renderWithAppProviders(<PolicyBuilder domainId="d1" domainName="example.com" onClose={() => {}} />);

  expect(await screen.findByRole("button", { name: /Also use a YetAnotherDmarcTool-hosted address/ })).toBeInTheDocument();
});
```

(`builder(...)`/`org(...)` are this file's existing mock helpers; if they're named differently, adapt to the names in the file and make `/onboarding/status` return `hosted_mailbox_ready: true`.)

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run --maxWorkers=1 src/components/policy-builder`
Expected: FAIL (address shown twice; no checkbox; no remove button).

- [ ] **Step 3: Implement** (DMARC builder; then the same in the TLS-RPT builder)

- `hostedMailboxAvailable` = `!!onboarding?.hosted_mailbox_ready` from `useOnboardingStatus()`; drop the `org` lookup if unused.
- Seeding (line ~108): the destination is ticked; a hosted address that isn't the destination is ticked only when it is already published:

```tsx
      const dest = data.org_mailbox_address;
      const hosted = data.hosted_report_address;
      const others = data.rua_destination.current_targets.filter((t) => !sameAddress(t, dest) && !sameAddress(t, hosted));
      const hostedPublished = !!hosted && data.rua_destination.current_targets.some((t) => sameAddress(t, hosted));
      const extraHosted = hosted && !sameAddress(hosted, dest) && hostedPublished ? [hosted] : [];
      setSelectedRua([...(dest ? [dest] : []), ...extraHosted, ...others]);
      setHostedAddress(hosted);
```
with a module-level `const sameAddress = (a: string | null | undefined, b: string | null | undefined) => !!a && !!b && a.toLowerCase() === b.toLowerCase();`. `otherTargets` uses the same filter (excludes destination and hosted).
- The hosted block (line ~196) becomes: when `hostedAddress && !sameAddress(hostedAddress, data.org_mailbox_address)`, a checkbox row labelled with the address + badge "hosted by YetAnotherDmarcTool", plus, when `data.has_own_mailbox`, a ghost button "Remove hosted address" calling `removeHosted.mutate()`; when `!hostedAddress && hostedMailboxAvailable`, the existing button with the label `data.org_mailbox_address ? "Also use a YetAnotherDmarcTool-hosted address" : "No mailbox of your own? Use a YetAnotherDmarcTool-hosted address"`. Requesting one no longer auto-ticks it when an own mailbox is the destination (`setSelectedRua` only adds it when `!data.org_mailbox_address`).
- The "no report destination yet" badge branch (line ~170) uses `hostedMailboxAvailable` as above.

`usePolicyBuilders.ts`:

```ts
export function useRemoveHostedReportAddress(domainId: string, onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete(`/domains/${domainId}/hosted-report-address`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.status });
      onSuccess?.();
    },
    onError,
  });
}
```
(Use the builder query keys this file already uses so the builders refetch too; check `api.delete` exists in `api/client.ts`, else use the same call `useRemoveProvider` uses.) On success the builder clears `hostedAddress` and drops it from `selectedRua`.

Add `has_own_mailbox: boolean` to both builder response types in `api/policyBuilder.ts`.

`overview/widgets.tsx:66-78`: replace the `entra_tenant_id || hosted_mailbox_opt_in` branch with the onboarding status:

```tsx
  if (!connection) {
    // Reports may still arrive at a hosted address on one of the domains.
    if (onboarding?.has_mailbox) return <span className="badge badge--good">Using hosted mailbox</span>;
    if (onboarding && !onboarding.hosted_mailbox_ready) {
      return <span className="badge badge--critical">No report mailbox on this server</span>;
    }
    return <span className="badge badge--warning">Mailbox not configured</span>;
  }
```

- [ ] **Step 4: Run to verify pass**

Run: `npx tsc -b && npx vitest run --maxWorkers=1 src/components src/pages`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components frontend/src/hooks/usePolicyBuilders.ts frontend/src/api/policyBuilder.ts
git commit -m "Builders: own mailbox first, hosted address once and removable; hosted for every org"
```

---

### Task 6: Wizard in dependency order

**Files:**
- Modify: `frontend/src/pages/Onboarding.tsx` (step list, `deriveStep`, `MailboxStep`, `DomainStep`/`VerifyStep` merge, new `SignInStep`, `HostedAddressPreview` removal, `useNoReportMailbox`), `frontend/src/api/onboarding.ts`
- Test: `frontend/src/pages/Onboarding.test.tsx` (rewrite the scenarios)

**Interfaces:**
- Consumes: `OnboardingStatus.has_sign_in_provider` (Task 3), `has_mailbox` real meaning (Task 3), `CopyValue` and `PendingDomains` (Task 4), `useGenerateHostedReportAddress` (existing), `SignInTab` (existing default export), `MailboxConnectionSection` (existing).
- Produces: exported `deriveStep(status: OnboardingStatus): number` (exported for tests).

- [ ] **Step 1: Write the failing tests**

Replace the "Onboarding without Microsoft sign-in" suite with:

```tsx
import Onboarding, { deriveStep } from "./Onboarding";

const base = {
  org_name: "Org", user_role: "org_admin" as const, has_mailbox: false, mailbox_consent_granted: false,
  mailbox_last_sync_status: null, has_domain: false, has_verified_domain: false, has_dns_baseline: false,
  has_any_report: false, hosted_mailbox_ready: true, has_sign_in_provider: false, wizard_completed: false,
};

describe("deriveStep", () => {
  it("starts at Welcome for an org that has done nothing", () => expect(deriveStep(base)).toBe(1));
  it("resumes at Domain while no domain is verified", () =>
    expect(deriveStep({ ...base, has_domain: true })).toBe(2));
  it("goes to Sign-in after the domain is verified", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true })).toBe(3));
  it("goes to Mailbox once a provider is set", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true, has_sign_in_provider: true })).toBe(4));
  it("treats sign-in as skipped once a report destination exists (Settings done first)", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true, has_mailbox: true })).toBe(5));
  it("goes to Reporting after the baseline", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true, has_mailbox: true, has_dns_baseline: true })).toBe(6));
});

describe("Report mailbox step", () => {
  it("creates nothing until a choice is made, and offers both choices", async () => {
    server({ verified: true, signInProvider: true });
    renderWithAppProviders(<Onboarding />);

    expect(await screen.findByRole("button", { name: /Use my own mailbox/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Use a YetAnotherDmarcTool-hosted address/ })).toBeInTheDocument();
    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith("/domains/d1/hosted-report-address");
    expect(screen.queryByRole("button", { name: /Continue/ })).not.toBeInTheDocument();
  });

  it("creates the hosted address only when clicked", async () => {
    server({ verified: true, signInProvider: true });
    vi.mocked(api.post).mockResolvedValueOnce({ hosted_report_address: "reports+abc@hosted.example" });
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Use a YetAnotherDmarcTool-hosted address/ }));
    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledWith("/domains/d1/hosted-report-address"));
    expect(await screen.findByText("reports+abc@hosted.example")).toBeInTheDocument();
  });

  it("hides the hosted choice when the server has none", async () => {
    server({ verified: true, signInProvider: true, hostedMailboxReady: false });
    renderWithAppProviders(<Onboarding />);
    expect(await screen.findByRole("button", { name: /Use my own mailbox/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /hosted address/ })).not.toBeInTheDocument();
  });
});

describe("Sign-in step", () => {
  it("can be skipped with local accounts only", async () => {
    server({ verified: true });
    renderWithAppProviders(<Onboarding />);
    fireEvent.click(await screen.findByRole("button", { name: /Only local accounts for now/ }));
    expect(await screen.findByRole("button", { name: /Use my own mailbox/ })).toBeInTheDocument();
  });
});
```

Update the file's `server()` helper: options `{ hostedMailboxReady = true, verified = false, signInProvider = false, ... }`, `/onboarding/status` returns `has_mailbox: false` (unless a mailbox/hosted option is set), `has_sign_in_provider: signInProvider`; add `/sign-in-settings` → `{ provider: signInProvider ? "google" : "none", oidc: null, can_change: true }` (check the real shape in `hooks/useSignInSettings.ts`). Keep the existing tests that still apply (baseline, reporting, waiting room), adjusting their expected step numbers to the new order; delete those asserting automatic hosted generation or "IMAP only" copy.

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run --maxWorkers=1 src/pages/Onboarding.test.tsx`
Expected: FAIL (`deriveStep` not exported; old step order).

- [ ] **Step 3: Implement**

`api/onboarding.ts`: add `has_sign_in_provider: boolean;`.

`Onboarding.tsx`:

```tsx
const STEP_LABELS = ["Welcome", "Domain", "Sign-in", "Report mailbox", "DNS baseline", "Reporting", "AI help", "Waiting room"];

// Resumes at the first unfinished step, judged only by what's actually set
// up, so whoever opens the wizard (break-glass or a later admin, after
// doing things in Settings) lands in the right place. Sign-in counts as
// done once a report destination exists: "Only local accounts for now"
// isn't stored, and getting past it is the only way to the mailbox step.
export function deriveStep(status: OnboardingStatus): number {
  const started = status.has_domain || status.has_mailbox || status.has_sign_in_provider;
  if (!started) return 1;
  if (!status.has_verified_domain) return 2;
  if (!status.has_sign_in_provider && !status.has_mailbox) return 3;
  if (!status.has_mailbox) return 4;
  if (!status.has_dns_baseline) return 5;
  return 6;
}
```

`setStep(deriveStep(status))` in the seeding effect (no `org` dependency any more). Step rendering:

```tsx
        {step === 1 && <WelcomeStep status={status} onNext={() => setStep(2)} />}
        {step === 2 && <DomainStep domain={focusDomain} onBack={() => setStep(1)} onNext={() => setStep(3)} />}
        {step === 3 && <SignInStep onBack={() => setStep(2)} onNext={() => setStep(4)} />}
        {step === 4 && <MailboxStep domain={focusDomain} onBack={() => setStep(3)} onNext={() => setStep(5)} />}
        {step === 5 && <BaselineStep domain={focusDomain} onBack={() => setStep(4)} onNext={() => setStep(6)} />}
        {step === 6 && <ReportingStep domain={focusDomain} onBack={() => setStep(5)} onNext={() => setStep(7)} />}
        {step === 7 && org && <AskAiStep org={org} onBack={() => setStep(6)} onNext={() => setStep(8)} />}
        {step === 8 && <WaitingRoomStep domain={focusDomain} onBack={() => setStep(7)} />}
```

Page subtitle: "Add and verify a domain, choose how people sign in and where reports go, and get a DNS baseline — a few minutes, most of it waiting on DNS."

`DomainStep` (merges old Domain + Verify): `<AddDomainForm />` when there's no apex domain yet; then the old `VerifyStep` body for `focusDomain` with the two `<strong>` values replaced by `<CopyValue label="Host/name" .../>` / `<CopyValue label="Value" .../>`; Continue enabled when `focusDomain?.verification_status === "verified"`. Delete `HostedAddressPreview` and the `hostedOnly` logic.

`SignInStep`:

```tsx
function SignInStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const { data } = useSignInSettings();
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>How people sign in</h2>
      <p className="section-hint">
        Let your team sign in with Microsoft, Google Workspace or your own OpenID Connect provider. Local accounts keep
        working next to it, and you can change this later in Settings → Sign-in.
      </p>
      <SignInTab />
      <div className="chip-row" style={{ marginTop: "1.25rem" }}>
        <button className="btn btn--ghost btn--sm" onClick={onBack}><ArrowLeft />Back</button>
        {data?.provider && data.provider !== "none" ? (
          <button className="btn btn--primary btn--sm" onClick={onNext}>Continue<ArrowRight /></button>
        ) : (
          <button className="btn btn--secondary btn--sm" onClick={onNext}>Only local accounts for now<ArrowRight /></button>
        )}
      </div>
    </div>
  );
}
```

`MailboxStep` (replaces both old branches; `focusDomain` passed in):

```tsx
function MailboxStep({ domain, onBack, onNext }: { domain: Domain | undefined; onBack: () => void; onNext: () => void }) {
  const { data: connection } = useMailboxConnection();
  const { data: status } = useOnboardingStatus();
  const [showOwn, setShowOwn] = useState(false);
  const [hosted, setHosted] = useState<string | null>(domain?.hosted_report_address ?? null);
  const [error, setError] = useState<string | null>(null);
  const generate = useGenerateHostedReportAddress(domain?.id ?? "", setHosted, (err) =>
    setError(err instanceof ApiError ? err.message : "couldn't create a hosted address"),
  );
  const hostedOffered = !!status?.hosted_mailbox_ready;
  const done = !!connection || !!hosted;

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Where reports arrive</h2>
      <p className="section-hint">
        DMARC reports are emailed by mail providers. Read them from a mailbox of your own (Microsoft 365, Google
        Workspace or IMAP){hostedOffered ? ", use an address hosted by YetAnotherDmarcTool, or both" : ""}.
      </p>
      <div className="chip-row">
        {!connection && !showOwn && (
          <button className="btn btn--secondary btn--sm" onClick={() => setShowOwn(true)}>Use my own mailbox</button>
        )}
        {hostedOffered && !hosted && domain && (
          <button className="btn btn--secondary btn--sm" onClick={() => generate.mutate()} disabled={generate.isPending}>
            Use a YetAnotherDmarcTool-hosted address
          </button>
        )}
      </div>
      {(connection || showOwn) && <MailboxConnectionSection canManage={true} />}
      {hosted && (
        <div className="alert alert--good" style={{ marginTop: "0.75rem" }}>
          Hosted address for {domain?.name}: <CopyValue value={hosted} />
        </div>
      )}
      {error && <div className="alert alert--critical">{error}</div>}
      {!hostedOffered && !connection && <NoReportMailboxNotice />}
      <StepNav onBack={onBack} onNext={done ? onNext : undefined} />
    </div>
  );
}
```

`useNoReportMailbox` becomes `!!status && !status.hosted_mailbox_ready && !status.has_mailbox` (no `org`). Update `NoReportMailboxNotice` copy to say "the Report mailbox step of this wizard, or Settings → General". `WelcomeStep`'s summary of the steps (if it lists them) follows the new order.

- [ ] **Step 4: Run to verify pass**

Run: `npx tsc -b && npx vitest run --maxWorkers=1 src/pages/Onboarding.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Onboarding.tsx frontend/src/pages/Onboarding.test.tsx frontend/src/api/onboarding.ts
git commit -m "Wizard in dependency order: domain, sign-in, report mailbox you choose"
```

---

### Task 7: Full verification, live check, beta21

**Files:** none new (release notes in the scratchpad).

- [ ] **Step 1: Full suites, one at a time**

Backend (expect ~775 passed, 0 failed), then frontend `npx tsc -b && npx vitest run --maxWorkers=1` (expect all passing). Note: the worktree's `node_modules` (prod folder) still has the pre-beta20 vite/vitest; CI's fresh install is authoritative for those versions.

- [ ] **Step 2: Live check on the local stack** (recipe in memory `yadt_local_testing.md`: scratch DB, uvicorn :8099, vite :5199, Playwright in Docker), test data only:
  1. Fresh local-auth org, org admin: wizard shows Welcome → Domain (add `wizard.example`, copy buttons copy the right values, mark verified in the scratch DB) → Sign-in ("Only local accounts for now") → Report mailbox: no Continue until a choice; "Use a hosted address" creates one (needs `HOSTED_REPORTS_DOMAIN`/`HOSTED_REPORTS_MAILBOX_ADDRESS` set to test values) → Continue.
  2. The reported path: second org, seed an IMAP/MailboxConnection row and a verified domain with a hosted address (as if done in Settings), open the wizard as a second admin: lands on step 5; DMARC builder shows the own mailbox, the hosted address once and unticked, "Remove hosted address" works and the address disappears.
  3. Settings → Domains lists a pending domain with copy buttons; Settings → General without a verified domain shows "Verify a domain first".
  4. Screenshots at 1280px and 390px wide; no horizontal scroll.
  Delete the temporary vite config and scratch DB afterwards.

- [ ] **Step 3: Push and CI**

`git push origin HEAD:v0.3.0-beta`; wait for backend-tests, frontend-build and CodeQL on the commit (`gh api repos/BJD1997/YetAnotherDmarcTool/commits/<sha>/check-runs`).

- [ ] **Step 4: Release v0.3.0-beta21**

Notes (scratchpad file), in the established style: "Pre-release for v0.3.0. Not for production use." — Fixed: own mailbox now used over a hosted address in the policy builders and DNS checks; hosted address no longer created automatically; no duplicate address in the builders; Settings → Domains shows pending domains; mailbox settings ask to verify a domain first. New: wizard order (domain → sign-in → report mailbox), choose own mailbox and/or hosted address, hosted addresses for every organization, remove a hosted address, copy buttons for verification records. "No database changes since beta20." Then `git tag -a v0.3.0-beta21 -m v0.3.0-beta21 <sha> && git push origin v0.3.0-beta21 && gh release create v0.3.0-beta21 --prerelease --verify-tag --notes-file <notes>`; confirm both GHCR images exist. Deploying to prod stays with the user.
