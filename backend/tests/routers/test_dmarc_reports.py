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
