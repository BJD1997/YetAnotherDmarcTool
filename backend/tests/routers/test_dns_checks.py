from datetime import datetime, timedelta, timezone

from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import CheckStatus, CheckType, TlsRptPolicyType, UserRole
from app.models.tls_rpt import TlsRptReport

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org, **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_latest_checks_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_latest_checks_returns_latest_run_only(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.fail,
                summary="stale finding", checked_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.pass_,
                summary="current finding", checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["summary"] == "current finding"


async def test_list_latest_checks_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    # entra=True on the discarded second user avoids colliding with the
    # partial unique index on lower(email) for auth_method='local' users —
    # seed_org_and_user hardcodes the same email for every call (see Task 4).
    other_org, _other_user = await seed_org_and_user(owner_factory, entra=True)
    domain = await _add_domain(owner_factory, other_org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 404


async def test_tls_rpt_summary_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_reports"] == 0
    assert body["failure_rate_pct"] is None


async def test_tls_rpt_reports_with_filters(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    # Seed multiple TlsRptReport rows with varying org_name and summary_failure_count
    async with owner_factory() as db:
        # Report 1: old date, org1, with failures
        db.add(
            TlsRptReport(
                organization_id=org.id,
                domain_id=domain.id,
                org_name="sender1.com",
                policy_domain=domain.name,
                policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=100),
                date_range_end=now - timedelta(days=99),
                summary_success_count=10,
                summary_failure_count=5,
                failure_details=[{"result_type": "certificate-expired", "failed_session_count": 3}],
                received_at=now - timedelta(days=99),
                created_at=now - timedelta(days=99),
            )
        )
        # Report 2: recent date, org1, no failures
        db.add(
            TlsRptReport(
                organization_id=org.id,
                domain_id=domain.id,
                org_name="sender1.com",
                policy_domain=domain.name,
                policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=5),
                date_range_end=now - timedelta(days=4),
                summary_success_count=20,
                summary_failure_count=0,
                failure_details=[],
                received_at=now - timedelta(days=4),
                created_at=now - timedelta(days=4),
            )
        )
        # Report 3: recent date, org2, with failures
        db.add(
            TlsRptReport(
                organization_id=org.id,
                domain_id=domain.id,
                org_name="sender2.com",
                policy_domain=domain.name,
                policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=3),
                date_range_end=now - timedelta(days=2),
                summary_success_count=15,
                summary_failure_count=7,
                failure_details=[{"result_type": "certificate-host-mismatch", "failed_session_count": 7}],
                received_at=now - timedelta(days=2),
                created_at=now - timedelta(days=2),
            )
        )
        await db.commit()

    await login_as(client, owner_factory, user)

    # Test: all reports
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3

    # Test: filter by days (last 10 days)
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?days=10")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2  # Only reports from last 10 days
    assert all(b["org_name"] in ["sender1.com", "sender2.com"] for b in body)

    # Test: filter by org_name
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?org_name=sender1")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2  # Both sender1.com reports
    assert all(b["org_name"] == "sender1.com" for b in body)

    # Test: filter by failures_only
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?failures_only=true")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2  # Only reports with failures (org1 old + org2 recent)
    assert all(b["failed_session_count"] > 0 for b in body)

    # Test: combined filters (days + org_name + failures_only)
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?days=10&org_name=sender2&failures_only=true")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["org_name"] == "sender2.com"
    assert body[0]["failed_session_count"] == 7


async def test_recheck_domain_requires_verified(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/checks/recheck")

    assert response.status_code == 409
