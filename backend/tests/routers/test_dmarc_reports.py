import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus, UserRole

from tests.conftest import login_as, seed_org_and_user


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


async def test_dmarc_reports_by_day_keyset_pagination_with_before_id(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)

    # Create records across two days with small limit to force pagination
    day1 = datetime.now(timezone.utc) - timedelta(days=2)
    day2 = datetime.now(timezone.utc) - timedelta(days=1)
    report1 = await _add_aggregate_report(owner_factory, org, domain, date_range_begin=day1)
    report2 = await _add_aggregate_report(owner_factory, org, domain, date_range_begin=day2)

    # Create multiple records on each day
    record1_day1 = await _add_aggregate_record(owner_factory, org, domain, report1, source_ip="203.0.113.1", count=1)
    record2_day1 = await _add_aggregate_record(owner_factory, org, domain, report1, source_ip="203.0.113.2", count=2)
    record1_day2 = await _add_aggregate_record(owner_factory, org, domain, report2, source_ip="203.0.113.3", count=3)
    record2_day2 = await _add_aggregate_record(owner_factory, org, domain, report2, source_ip="203.0.113.4", count=4)

    # Fetch first page with limit=2 (should get 2 records from day2)
    response1 = await client.get(f"/api/domains/{domain.id}/dmarc/reports/by-day?limit=2")
    assert response1.status_code == 200
    body1 = response1.json()
    assert body1["has_more"] is True
    page1_records = body1["days"][0]["rows"]
    assert len(page1_records) == 2
    first_page_ids = {r["record_id"] for r in page1_records}

    # Fetch second page using last record's ID as before_id cursor
    last_record_id = page1_records[-1]["record_id"]
    response2 = await client.get(f"/api/domains/{domain.id}/dmarc/reports/by-day?limit=2&before_id={last_record_id}")
    assert response2.status_code == 200
    body2 = response2.json()
    page2_records = []
    for day in body2["days"]:
        page2_records.extend(day["rows"])

    # Verify no record from page1 appears on page2 (keyset cursor excludes them)
    page2_ids = {r["record_id"] for r in page2_records}
    assert len(first_page_ids & page2_ids) == 0, "Page 2 should not contain records from Page 1"


async def test_dmarc_record_detail_404_for_different_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    # Create two domains in same org
    domain_a = await _add_domain(owner_factory, org, name="example-a.com")
    domain_b = await _add_domain(owner_factory, org, name="example-b.com")

    # Create a report and record under domain A
    report_a = await _add_aggregate_report(owner_factory, org, domain_a)
    record_a = await _add_aggregate_record(owner_factory, org, domain_a, report_a, source_ip="203.0.113.10", count=5)

    # Try to access domain A's record via domain B's endpoint (should 404)
    response = await client.get(f"/api/domains/{domain_b.id}/dmarc/records/{record_a.id}")

    assert response.status_code == 404
