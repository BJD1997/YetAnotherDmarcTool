"""Cross-tenant access & privilege-escalation isolation sweep.

Seeds two orgs. Org B owns a domain, an aggregate report + record, and a DKIM
selector. Then, authenticated as org A's admin, it calls every ID-taking route
using org B's IDs and asserts each one is refused (404) — never leaks or mutates
another tenant's data. A second section proves an org member can't reach the
org_admin-only routes (403).

Belt-and-suspenders on top of test_rls.py: that proves the DB policy in
isolation; this proves the HTTP routes actually keep the caller inside their own
org even when handed valid IDs from another. Gated on TEST_DATABASE_URL like the
rest of the DB suite (see conftest.py).
"""

import uuid
from datetime import datetime, timezone

from app.models.dkim_selector import DkimSelector
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import (
    AuthResult,
    Disposition,
    DomainVerificationStatus,
    UserRole,
    UserStatus,
)
from app.models.user import User

from tests.conftest import login_as, seed_org_and_user


async def _seed_victim_org(owner_factory):
    """Org B with a full set of addressable child rows for org A to try to reach."""
    org_b, admin_b = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        # A second member, so reset/patch routes have a non-admin target too.
        member_b = User(
            organization_id=org_b.id,
            email=f"member-b+{uuid.uuid4()}@victim.example",
            display_name="Victim Member",
            role=UserRole.member,
            status=UserStatus.active,
        )
        domain_b = Domain(
            organization_id=org_b.id,
            name=f"victim-{uuid.uuid4().hex[:8]}.example.com",
            verification_status=DomainVerificationStatus.verified,
            verified_at=now,
        )
        db.add_all([member_b, domain_b])
        await db.flush()

        report_b = DmarcAggregateReport(
            organization_id=org_b.id,
            domain_id=domain_b.id,
            report_id="victim-report-1",
            org_name="google.com",
            date_range_begin=now,
            date_range_end=now,
            policy_published_domain=domain_b.name,
            received_at=now,
        )
        db.add(report_b)
        await db.flush()
        record_b = DmarcAggregateRecord(
            organization_id=org_b.id,
            report_id=report_b.id,
            domain_id=domain_b.id,
            source_ip="203.0.113.9",
            count=5,
            disposition=Disposition.none,
            dkim_result=AuthResult.fail,
            spf_result=AuthResult.fail,
            header_from=domain_b.name,
            auth_results={},
            created_at=now,
        )
        selector_b = DkimSelector(
            organization_id=org_b.id, domain_id=domain_b.id, selector="s1", description="victim"
        )
        db.add_all([record_b, selector_b])
        await db.flush()
        for obj in (member_b, domain_b, report_b, record_b, selector_b):
            await db.refresh(obj)
        await db.commit()
        return {
            "org": org_b,
            "member": member_b,
            "domain": domain_b,
            "report": report_b,
            "record": record_b,
            "selector": selector_b,
        }


def _cross_org_requests(b) -> list[tuple[str, str]]:
    """(method, path) for every ID-taking org-scoped route, addressing org B."""
    d = str(b["domain"].id)
    return [
        ("GET", f"/api/domains/{d}"),
        ("PATCH", f"/api/domains/{d}"),
        ("DELETE", f"/api/domains/{d}"),
        ("POST", f"/api/domains/{d}/verify"),
        ("POST", f"/api/domains/{d}/hosted-report-address"),
        ("GET", f"/api/domains/{d}/checks"),
        ("POST", f"/api/domains/{d}/checks/recheck"),
        ("GET", f"/api/domains/{d}/dmarc/inbound"),
        ("GET", f"/api/domains/{d}/dns/mta-sts-builder"),
        ("GET", f"/api/domains/{d}/dns/tls-rpt-builder"),
        ("GET", f"/api/domains/{d}/dmarc/rua-check"),
        ("GET", f"/api/domains/{d}/dmarc/summary"),
        ("GET", f"/api/domains/{d}/rating"),
        ("GET", f"/api/domains/{d}/dmarc/sources"),
        ("GET", f"/api/domains/{d}/dmarc/sender-inventory"),
        ("PATCH", f"/api/domains/{d}/dmarc/sender-inventory/some-label"),
        ("GET", f"/api/domains/{d}/dmarc/reports/by-day"),
        ("GET", f"/api/domains/{d}/dmarc/reports/summary"),
        ("GET", f"/api/domains/{d}/dmarc/reports/grouped"),
        ("GET", f"/api/domains/{d}/dmarc/records/{b['record'].id}"),
        ("GET", f"/api/domains/{d}/dmarc/policy-builder"),
        ("GET", f"/api/domains/{d}/dmarc/tls-rpt/summary"),
        ("GET", f"/api/domains/{d}/dmarc/tls-rpt/reports"),
        ("GET", f"/api/domains/{d}/dmarc/tls-rpt/by-sender"),
        ("GET", f"/api/domains/{d}/selectors"),
        ("GET", f"/api/domains/{d}/selectors/detected"),
        ("DELETE", f"/api/domains/{d}/selectors/{b['selector'].id}"),
        ("PATCH", f"/api/users/{b['member'].id}"),
        ("POST", f"/api/users/{b['member'].id}/reset-password"),
        ("POST", f"/api/users/{b['member'].id}/reset-mfa"),
    ]


async def test_org_admin_cannot_touch_another_orgs_resources(api):
    client, owner_factory = api
    b = await _seed_victim_org(owner_factory)
    # Org A seeded inline (not via seed_org_and_user, whose hardcoded email would
    # collide with org B's admin on the local-email unique index).
    from app.models.enums import OrganizationStatus
    from app.models.organization import Organization

    async with owner_factory() as db:
        org_a = Organization(name="Attacker Org", status=OrganizationStatus.active)
        db.add(org_a)
        await db.flush()
        admin_a = User(
            organization_id=org_a.id,
            email=f"admin-a+{uuid.uuid4()}@attacker.example",
            display_name="Attacker Admin",
            role=UserRole.org_admin,
            status=UserStatus.active,
        )
        db.add(admin_a)
        await db.flush()
        await db.refresh(admin_a)
        await db.commit()
    await login_as(client, owner_factory, admin_a)

    leaks = []
    for method, path in _cross_org_requests(b):
        resp = await client.request(method, path, json={} if method in ("PATCH", "POST") else None)
        # 404 is the correct "not in your org" answer. 422 (bad body) would also
        # mean the resource was never reached; anything 2xx/3xx is a real leak.
        if resp.status_code not in (404, 422):
            leaks.append(f"{method} {path} -> {resp.status_code}")

    # Nothing in org B should have been mutated by the calls above.
    async with owner_factory() as db:
        still_there = await db.get(Domain, b["domain"].id)
    assert still_there is not None, "org A's DELETE reached org B's domain"
    assert leaks == [], f"cross-org access not refused: {leaks}"


async def test_member_cannot_reach_admin_only_routes(api):
    client, owner_factory = api
    org_a, _admin_a = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        member_a = User(
            organization_id=org_a.id,
            email=f"member-a+{uuid.uuid4()}@a.example",
            display_name="Member A",
            role=UserRole.member,
            status=UserStatus.active,
        )
        domain_a = Domain(organization_id=org_a.id, name=f"a-{uuid.uuid4().hex[:8]}.example.com")
        db.add_all([member_a, domain_a])
        await db.flush()
        await db.refresh(member_a)
        await db.refresh(domain_a)
        await db.commit()
    await login_as(client, owner_factory, member_a)

    d = str(domain_a.id)
    admin_only = [
        ("POST", "/api/domains", {"name": "escalate.example.com"}),
        ("PATCH", f"/api/domains/{d}", {"notes": "x"}),
        ("DELETE", f"/api/domains/{d}", None),
        ("POST", f"/api/domains/{d}/verify", None),
        ("POST", "/api/users", {"email": "new@a.example"}),
        ("PATCH", f"/api/users/{member_a.id}", {"role": "org_admin"}),
        ("GET", "/api/sign-in-events", None),
    ]
    escalations = []
    for method, path, body in admin_only:
        resp = await client.request(method, path, json=body)
        if resp.status_code != 403:
            escalations.append(f"{method} {path} -> {resp.status_code}")
    assert escalations == [], f"member reached admin-only route: {escalations}"
