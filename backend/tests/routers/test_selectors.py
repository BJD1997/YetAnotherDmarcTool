from app.models.domain import Domain
from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_selectors_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/selectors")

    assert response.status_code == 200
    assert response.json() == []


async def test_create_selector(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    assert response.status_code == 201
    assert response.json()["selector"] == "google"


async def test_create_selector_rejects_invalid_format(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "not valid!"})

    assert response.status_code == 422


async def test_create_duplicate_selector_conflicts(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)
    await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    assert response.status_code == 409


async def test_delete_selector(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)
    created = (await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})).json()

    response = await client.delete(f"/api/domains/{domain.id}/selectors/{created['id']}")

    assert response.status_code == 204


async def test_detected_selectors_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/selectors/detected")

    assert response.status_code == 200
    assert response.json() == []


async def test_detected_selectors_leave_out_blocked_senders(api):
    """A selector only seen on mail from blocked senders isn't worth adding;
    one also used by a sender that isn't blocked still shows."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
    from app.models.enums import AuthResult, Disposition, SenderReviewStatus, SourceMatchMethod
    from app.models.sender_review import SenderReview
    from app.models.source_ip_identity import SourceIpIdentity

    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        report = DmarcAggregateReport(
            organization_id=org.id, domain_id=domain.id, report_id=str(uuid.uuid4()), org_name="google.com",
            date_range_begin=now - timedelta(days=2), date_range_end=now - timedelta(days=1),
            policy_published_domain=domain.name, policy_p="reject", received_at=now, created_at=now,
        )
        db.add(report)
        await db.flush()
        for ip, label, selectors in (
            ("203.0.113.40", "spoofer.example", ["blockedonly", "shared"]),
            ("203.0.113.41", "mailer.example", ["goodsel", "shared"]),
        ):
            await db.merge(SourceIpIdentity(source_ip=ip, service_label=label, match_method=SourceMatchMethod.ptr_domain, resolved_at=now))
            db.add(
                DmarcAggregateRecord(
                    organization_id=org.id, report_id=report.id, domain_id=domain.id, source_ip=ip, count=5,
                    disposition=Disposition.none, dkim_result=AuthResult.pass_, spf_result=AuthResult.pass_,
                    header_from=domain.name, created_at=now,
                    auth_results={"dkim": [{"selector": s, "domain": domain.name, "result": "pass"} for s in selectors]},
                )
            )
        db.add(SenderReview(organization_id=org.id, domain_id=domain.id, service_label="spoofer.example", status=SenderReviewStatus.blocked))
        await db.commit()
    await login_as(client, owner_factory, user)

    found = {s["selector"]: s["message_volume"] for s in (await client.get(f"/api/domains/{domain.id}/selectors/detected")).json()}

    assert found == {"goodsel": 5, "shared": 5}
