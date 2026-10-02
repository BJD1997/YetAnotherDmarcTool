"""The selector source behind DKIM-selector notifications: aligned, not yet
monitored, from the last 30 days, with the sender that signs with it."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.db.rls import set_org_context
from app.models.dkim_selector import DkimSelector
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus
from app.services.notifications.sources import new_selectors

from tests.conftest import seed_org_and_user


@pytest_asyncio.fixture(autouse=True)
async def _no_ptr(monkeypatch):
    from app.services.source_identification import service_identifier

    async def _none(ip: str):
        return None

    monkeypatch.setattr(service_identifier, "resolve_ptr", _none)


async def _record(db, org, domain, days_ago, source_ip, dkim):
    begin = datetime.now(timezone.utc) - timedelta(days=days_ago)
    report = DmarcAggregateReport(
        organization_id=org.id, domain_id=domain.id, report_id=str(uuid.uuid4()), org_name="google.com",
        date_range_begin=begin, date_range_end=begin + timedelta(days=1), policy_published_domain=domain.name,
        policy_p="reject", received_at=begin, created_at=begin,
    )
    db.add(report)
    await db.flush()
    db.add(
        DmarcAggregateRecord(
            organization_id=org.id, report_id=report.id, domain_id=domain.id, source_ip=source_ip, count=7,
            disposition=Disposition.none, dkim_result=AuthResult.pass_, spf_result=AuthResult.pass_,
            header_from=domain.name, auth_results={"dkim": dkim}, created_at=begin,
        )
    )


async def test_new_selectors_are_aligned_unmonitored_and_recent(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified)
        db.add(domain)
        await db.flush()
        db.add(DkimSelector(organization_id=org.id, domain_id=domain.id, selector="known"))
        await _record(db, org, domain, 2, "203.0.113.20", [
            {"selector": "new1", "domain": "example.com", "result": "pass"},
            {"selector": "known", "domain": "example.com", "result": "pass"},
            {"selector": "esp", "domain": "esp-unrelated.net", "result": "pass"},
        ])
        await _record(db, org, domain, 45, "203.0.113.21", [{"selector": "stale", "domain": "example.com", "result": "pass"}])
        await db.commit()

    async with owner_factory() as db:
        await set_org_context(db, org.id)
        findings = await new_selectors(db, org.id)
        await db.rollback()

    assert [(f.selector, f.message_volume, f.sender_label) for f in findings] == [("new1", 7, "203.0.113.20")]


async def test_selectors_only_blocked_senders_use_are_left_out(api):
    from app.models.enums import SenderReviewStatus, SourceMatchMethod
    from app.models.sender_review import SenderReview
    from app.models.source_ip_identity import SourceIpIdentity

    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified)
        db.add(domain)
        await db.flush()
        await db.merge(SourceIpIdentity(source_ip="203.0.113.50", service_label="spoofer.example", match_method=SourceMatchMethod.ptr_domain, resolved_at=now))
        db.add(SenderReview(organization_id=org.id, domain_id=domain.id, service_label="spoofer.example", status=SenderReviewStatus.blocked))
        await _record(db, org, domain, 2, "203.0.113.50", [{"selector": "spoofsel", "domain": "example.com", "result": "pass"}])
        await db.commit()

    async with owner_factory() as db:
        await set_org_context(db, org.id)
        findings = await new_selectors(db, org.id)
        await db.rollback()

    assert findings == []
