from datetime import datetime, timezone

from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import CheckStatus, CheckType, UserRole

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


async def test_recheck_domain_requires_verified(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/checks/recheck")

    assert response.status_code == 409
