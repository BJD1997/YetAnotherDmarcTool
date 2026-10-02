"""Notifications API: everyone in the org reads, org admins dismiss, and a
selector's link follows the signing sender's review status."""

import uuid

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus, OrganizationStatus, SenderReviewStatus, UserRole
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.sender_review import SenderReview

from tests.conftest import login_as, seed_org_and_user


async def _seed(owner_factory, role):
    org, user = await seed_org_and_user(owner_factory, role=role)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified)
        db.add(domain)
        await db.flush()
        db.add(
            Notification(
                id=uuid.uuid4(), organization_id=org.id, kind="domain_detected", subject_key="other-brand.example",
                payload={"message_volume": 12, "relationship": "apex"},
            )
        )
        db.add(
            Notification(
                id=uuid.uuid4(), organization_id=org.id, kind="dkim_selector_detected", subject_key=f"{domain.id}:s1",
                domain_id=domain.id,
                payload={"domain_name": "example.com", "selector": "s1", "message_volume": 7, "sender_label": "mailer.example"},
            )
        )
        await db.commit()
    return org, user, domain


async def test_members_see_notifications_and_links(api):
    client, owner_factory = api
    org, user, domain = await _seed(owner_factory, UserRole.member)
    await login_as(client, owner_factory, user)

    assert (await client.get("/api/notifications/count")).json() == {"open": 2}
    items = {n["kind"]: n for n in (await client.get("/api/notifications")).json()}
    assert items["domain_detected"]["title"] == "New domain in your reports: other-brand.example"
    assert items["domain_detected"]["link_path"] == "/settings/domains"
    selector = items["dkim_selector_detected"]
    assert selector["title"] == "example.com signs with an unmonitored DKIM selector: s1"
    assert selector["link_path"] == f"/domains/{domain.id}/senders?highlight=mailer.example"
    assert "isn't approved yet" in selector["detail"]

    async with owner_factory() as db:
        db.add(SenderReview(organization_id=org.id, domain_id=domain.id, service_label="mailer.example", status=SenderReviewStatus.approved))
        await db.commit()
    selector = {n["kind"]: n for n in (await client.get("/api/notifications")).json()}["dkim_selector_detected"]
    assert selector["link_path"] == f"/domains/{domain.id}/dns?open=dkim-selectors"

    assert (await client.post(f"/api/notifications/{selector['id']}/dismiss")).status_code == 403


async def test_admins_dismiss(api):
    client, owner_factory = api
    _org, user, _domain = await _seed(owner_factory, UserRole.org_admin)
    await login_as(client, owner_factory, user)

    first = (await client.get("/api/notifications")).json()[0]
    assert (await client.post(f"/api/notifications/{first['id']}/dismiss")).status_code == 204
    assert (await client.get("/api/notifications/count")).json() == {"open": 1}
    dismissed = next(n for n in (await client.get("/api/notifications")).json() if n["id"] == first["id"])
    assert dismissed["resolved_reason"] == "dismissed"


async def test_other_orgs_notification_is_not_found(api):
    client, owner_factory = api
    _org, user, _domain = await _seed(owner_factory, UserRole.org_admin)
    async with owner_factory() as db:
        other = Organization(name="Other Org", status=OrganizationStatus.active)
        db.add(other)
        await db.flush()
        foreign = Notification(id=uuid.uuid4(), organization_id=other.id, kind="domain_detected", subject_key="x.example", payload={})
        db.add(foreign)
        await db.commit()
    await login_as(client, owner_factory, user)

    assert (await client.post(f"/api/notifications/{foreign.id}/dismiss")).status_code == 404
    assert all(n["id"] != str(foreign.id) for n in (await client.get("/api/notifications")).json())
