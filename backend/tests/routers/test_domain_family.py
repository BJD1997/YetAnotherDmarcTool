"""Adding a detected domain brings its family along (its root domain first,
the root's other detected subdomains under it), and an unverified domain can
be removed, handing its reports back."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus, UserRole

from tests.conftest import login_as, seed_org_and_user
from tests.routers.test_dmarc_reports import _add_aggregate_record, _add_aggregate_report, _add_domain, _fast_ip_fallback  # noqa: F401


async def _admin(client, owner_factory):
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)
    return org


async def _seen(owner_factory, org, *names):
    """Unmatched reports for these names, as if the mailbox received them."""
    for name in names:
        report = await _add_aggregate_report(owner_factory, org, None, org_name="google.com")
        async with owner_factory() as db:
            row = await db.get(DmarcAggregateReport, report.id)
            row.policy_published_domain = name
            await db.commit()
        await _add_aggregate_record(owner_factory, org, None, report, header_from=name, count=5)


async def _domains(owner_factory, org):
    async with owner_factory() as db:
        rows = (await db.execute(select(Domain).where(Domain.organization_id == org.id))).scalars().all()
        by_id = {d.id: d.name for d in rows}
        return {d.name: (by_id.get(d.parent_domain_id), d.verification_status.value) for d in rows}


async def test_subdomain_brings_its_detected_root_and_siblings(api):
    client, owner_factory = api
    org = await _admin(client, owner_factory)
    await _seen(owner_factory, org, "hosting-test.nl", "web02.hosting-test.nl", "web01.hosting-test.nl")

    response = await client.post("/api/domains/detected", json={"name": "web02.hosting-test.nl"})

    assert response.status_code == 201, response.text
    assert response.json()["added"][0] == "hosting-test.nl"
    assert await _domains(owner_factory, org) == {
        "hosting-test.nl": (None, "pending"),
        "web02.hosting-test.nl": ("hosting-test.nl", "pending"),
        "web01.hosting-test.nl": ("hosting-test.nl", "pending"),
    }
    detected = (await client.get("/api/dmarc/detected-domains")).json()
    assert detected == []


async def test_without_a_detected_root_the_subdomain_is_added_on_its_own(api):
    client, owner_factory = api
    org = await _admin(client, owner_factory)
    await _seen(owner_factory, org, "web02.hosting-test.nl")

    detected = (await client.get("/api/dmarc/detected-domains")).json()
    assert detected[0]["relationship"] == "apex" and detected[0]["suggested_parent_name"] is None

    response = await client.post("/api/domains/detected", json={"name": "web02.hosting-test.nl"})

    assert response.json()["added"] == ["web02.hosting-test.nl"]
    assert await _domains(owner_factory, org) == {"web02.hosting-test.nl": (None, "pending")}


async def test_a_root_domain_added_later_takes_in_its_subdomains(api):
    client, owner_factory = api
    org = await _admin(client, owner_factory)
    await _add_domain(
        owner_factory, org, name="web02.hosting-test.nl",
        verification_status=DomainVerificationStatus.verified, verified_at=datetime.now(timezone.utc),
    )
    await _seen(owner_factory, org, "hosting-test.nl")

    response = await client.post("/api/domains/detected", json={"name": "hosting-test.nl"})

    assert response.json()["moved"] == ["web02.hosting-test.nl"]
    domains = await _domains(owner_factory, org)
    assert domains["web02.hosting-test.nl"] == ("hosting-test.nl", "verified")  # keeps its verification


async def test_verifying_the_root_verifies_its_subdomains(api, monkeypatch):
    from app.services.dns_checks import domain_verification

    async def _owned(_name, _token):
        return True

    monkeypatch.setattr(domain_verification, "verify_domain_ownership", _owned)
    client, owner_factory = api
    org = await _admin(client, owner_factory)
    await _seen(owner_factory, org, "hosting-test.nl", "web02.hosting-test.nl")
    await client.post("/api/domains/detected", json={"name": "web02.hosting-test.nl"})
    root_id = next(d["id"] for d in (await client.get("/api/domains")).json() if d["name"] == "hosting-test.nl")

    await client.post(f"/api/domains/{root_id}/verify")

    assert (await _domains(owner_factory, org))["web02.hosting-test.nl"][1] == "verified"


async def test_removing_an_unverified_domain_hands_its_reports_back(api):
    client, owner_factory = api
    org = await _admin(client, owner_factory)
    await _seen(owner_factory, org, "web02.hosting-test.nl")
    created = await client.post("/api/domains", json={"name": "web02.hosting-test.nl"})
    domain_id = created.json()["id"]
    assert created.json()["reattributed_reports"] == 1

    response = await client.delete(f"/api/domains/{domain_id}")

    assert response.status_code == 204, response.text
    async with owner_factory() as db:
        reports = (await db.execute(select(DmarcAggregateReport.domain_id).where(DmarcAggregateReport.organization_id == org.id))).scalars().all()
        records = (await db.execute(select(DmarcAggregateRecord.domain_id).where(DmarcAggregateRecord.organization_id == org.id))).scalars().all()
    assert reports == [None] and records == [None]
    assert [d["name"] for d in (await client.get("/api/dmarc/detected-domains")).json()] == ["web02.hosting-test.nl"]


async def test_removed_subdomain_hands_its_reports_to_its_parent(api):
    client, owner_factory = api
    org = await _admin(client, owner_factory)
    parent = await _add_domain(owner_factory, org, name="hosting-test.nl")
    await _seen(owner_factory, org, "web02.hosting-test.nl")
    created = await client.post("/api/domains", json={"name": "web02.hosting-test.nl", "parent_domain_id": str(parent.id)})

    await client.delete(f"/api/domains/{created.json()['id']}")

    async with owner_factory() as db:
        records = (await db.execute(select(DmarcAggregateRecord.domain_id).where(DmarcAggregateRecord.organization_id == org.id))).scalars().all()
    assert records == [parent.id]


async def test_a_verified_domain_with_reports_is_archived_not_removed(api):
    client, owner_factory = api
    org = await _admin(client, owner_factory)
    domain = await _add_domain(
        owner_factory, org, name="hosting-test.nl",
        verification_status=DomainVerificationStatus.verified, verified_at=datetime.now(timezone.utc),
    )
    await _add_aggregate_report(owner_factory, org, domain)

    response = await client.delete(f"/api/domains/{domain.id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "This domain has report history, so it can't be removed. Archive it instead."
