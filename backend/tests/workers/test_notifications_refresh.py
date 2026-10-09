"""notifications_refresh: creates a notification once per new domain or
selector, and resolves it once it's handled."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select

from app.db.rls import set_org_context
from app.models.dismissed_detected_domain import DismissedDetectedDomain
from app.models.dkim_selector import DkimSelector
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus, SenderReviewStatus
from app.models.notification import Notification
from app.models.sender_review import SenderReview
from app.services.notifications.refresh import refresh_org_notifications

from tests.conftest import seed_org_and_user


@pytest_asyncio.fixture(autouse=True)
async def _no_ptr(monkeypatch):
    from app.services.source_identification import service_identifier

    async def _none(ip: str):
        return None

    monkeypatch.setattr(service_identifier, "resolve_ptr", _none)


async def _seed(owner_factory):
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified)
        db.add(domain)
        await db.flush()
        for target_domain, header_from, dkim in (
            (domain, "example.com", [{"selector": "new1", "domain": "example.com", "result": "pass"}]),
            (None, "other-brand.example", []),
        ):
            report = DmarcAggregateReport(
                organization_id=org.id, domain_id=target_domain.id if target_domain else None,
                report_id=str(uuid.uuid4()), org_name="google.com", date_range_begin=now - timedelta(days=2),
                date_range_end=now - timedelta(days=1), policy_published_domain=header_from, policy_p="reject",
                received_at=now, created_at=now,
            )
            db.add(report)
            await db.flush()
            db.add(
                DmarcAggregateRecord(
                    organization_id=org.id, report_id=report.id, domain_id=target_domain.id if target_domain else None,
                    source_ip="203.0.113.30", count=9, disposition=Disposition.none, dkim_result=AuthResult.pass_,
                    spf_result=AuthResult.pass_, header_from=header_from, auth_results={"dkim": dkim}, created_at=now,
                )
            )
        await db.commit()
    return org, domain


async def _refresh(owner_factory, org):
    async with owner_factory() as db:
        await set_org_context(db, org.id)
        result = await refresh_org_notifications(db, org.id)
        await db.commit()
    return result


async def _notifications(owner_factory, org):
    async with owner_factory() as db:
        rows = (await db.execute(select(Notification).where(Notification.organization_id == org.id))).scalars().all()
    return {(n.kind, n.subject_key): n for n in rows}


async def test_creates_once_and_resolves_when_handled(api):
    _client, owner_factory = api
    org, domain = await _seed(owner_factory)

    assert await _refresh(owner_factory, org) == (2, 0)
    assert await _refresh(owner_factory, org) == (0, 0)
    found = await _notifications(owner_factory, org)
    assert set(found) == {("domain_detected", "other-brand.example"), ("dkim_selector_detected", f"{domain.id}:new1")}
    selector = found[("dkim_selector_detected", f"{domain.id}:new1")]
    assert selector.payload["sender_label"] == "203.0.113.30" and selector.payload["selector"] == "new1"

    async with owner_factory() as db:
        db.add(DismissedDetectedDomain(organization_id=org.id, name="other-brand.example"))
        db.add(DkimSelector(organization_id=org.id, domain_id=domain.id, selector="new1"))
        await db.commit()

    assert await _refresh(owner_factory, org) == (0, 2)
    assert {n.resolved_reason for n in (await _notifications(owner_factory, org)).values()} == {"handled"}


async def test_selector_of_a_blocked_sender_resolves(api):
    _client, owner_factory = api
    org, domain = await _seed(owner_factory)
    await _refresh(owner_factory, org)

    async with owner_factory() as db:
        db.add(
            SenderReview(organization_id=org.id, domain_id=domain.id, service_label="203.0.113.30", status=SenderReviewStatus.blocked)
        )
        await db.commit()

    await _refresh(owner_factory, org)
    selector = (await _notifications(owner_factory, org))[("dkim_selector_detected", f"{domain.id}:new1")]
    assert selector.resolved_reason == "handled"


async def test_dismissed_domain_is_never_notified(api):
    _client, owner_factory = api
    org, _domain = await _seed(owner_factory)
    async with owner_factory() as db:
        db.add(DismissedDetectedDomain(organization_id=org.id, name="other-brand.example"))
        await db.commit()

    await _refresh(owner_factory, org)
    assert ("domain_detected", "other-brand.example") not in await _notifications(owner_factory, org)
