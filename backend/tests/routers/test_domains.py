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


async def test_list_domains_empty(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/domains")

    assert response.status_code == 200
    assert response.json() == []


async def test_create_domain(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/domains", json={"name": "example.com"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "example.com"
    assert body["verification_status"] == "pending"


async def test_create_domain_rejects_invalid_name(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/domains", json={"name": "not a domain"})

    assert response.status_code == 422


async def test_get_domain_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_get_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "example.com"


async def test_update_domain_requires_admin(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/domains/{domain.id}", json={"notes": "hi"})

    assert response.status_code == 403


async def test_update_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/domains/{domain.id}", json={"notes": "hi", "is_active": False})

    assert response.status_code == 200
    body = response.json()
    assert body["notes"] == "hi"
    assert body["is_active"] is False


async def test_delete_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.delete(f"/api/domains/{domain.id}")

    assert response.status_code == 204


async def test_delete_domain_blocked_by_subdomain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    parent = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)
    await _add_domain(owner_factory, org, name="sub.example.com", parent_domain_id=parent.id)
    await login_as(client, owner_factory, user)

    response = await client.delete(f"/api/domains/{parent.id}")

    assert response.status_code == 409


async def test_ranked_domains_includes_unverified_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/domains/ranked")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["not_verified"] is True


async def test_ranked_domains_message_and_failed_volume_share_the_same_window(api):
    """Regression test: failed_volume used to come from a separate,
    all-time, unfiltered query while message_volume was windowed to the
    last RATING_WINDOW_DAYS (90) and excluded blocked-sender traffic — two
    different populations shown in the same row, which is how a real
    browser review found failed_volume exceeding message_volume. Both now
    come from the same compute_domain_rating call, so an old (>90d)
    record's volume must not appear in either total."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)
    await login_as(client, owner_factory, user)

    now = datetime.now(timezone.utc)
    old_date = now - timedelta(days=200)  # well outside the 90-day rating window

    async with owner_factory() as db:
        old_report = DmarcAggregateReport(
            organization_id=org.id,
            domain_id=domain.id,
            report_id=str(uuid.uuid4()),
            org_name="google.com",
            date_range_begin=old_date,
            date_range_end=old_date,
            policy_published_domain=domain.name,
            policy_p="reject",
            received_at=now,
        )
        db.add(old_report)
        await db.flush()
        db.add(
            DmarcAggregateRecord(
                organization_id=org.id,
                report_id=old_report.id,
                domain_id=domain.id,
                source_ip="203.0.113.99",
                count=9000,
                disposition=Disposition.reject,
                dkim_result=AuthResult.fail,
                spf_result=AuthResult.fail,
                header_from=domain.name,
                auth_results={},
                created_at=now,
            )
        )

        recent_report = DmarcAggregateReport(
            organization_id=org.id,
            domain_id=domain.id,
            report_id=str(uuid.uuid4()),
            org_name="google.com",
            date_range_begin=now - timedelta(days=1),
            date_range_end=now,
            policy_published_domain=domain.name,
            policy_p="reject",
            received_at=now,
        )
        db.add(recent_report)
        await db.flush()
        db.add(
            DmarcAggregateRecord(
                organization_id=org.id,
                report_id=recent_report.id,
                domain_id=domain.id,
                source_ip="203.0.113.10",
                count=100,
                disposition=Disposition.none,
                dkim_result=AuthResult.pass_,
                spf_result=AuthResult.pass_,
                header_from=domain.name,
                auth_results={},
                created_at=now,
            )
        )
        db.add(
            DmarcAggregateRecord(
                organization_id=org.id,
                report_id=recent_report.id,
                domain_id=domain.id,
                source_ip="203.0.113.11",
                count=50,
                disposition=Disposition.reject,
                dkim_result=AuthResult.fail,
                spf_result=AuthResult.fail,
                header_from=domain.name,
                auth_results={},
                created_at=now,
            )
        )
        await db.commit()

    response = await client.get("/api/domains/ranked")

    assert response.status_code == 200
    body = response.json()[0]
    # Only the recent (within-90-day) traffic counts — the 9,000-message
    # July-era record must not inflate either total.
    assert body["message_volume"] == 150
    assert body["failed_volume"] == 50
    assert body["failed_volume"] <= body["message_volume"]
