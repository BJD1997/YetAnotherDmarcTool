""""Likely spoofed": unauthenticated mail that receivers blocked — judged only
on mail sent while the domain enforced DMARC, since under p=none receivers
deliver spoofed mail as told and their choice says nothing."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus
from app.services.dmarc_analytics import service_breakdown

from tests.conftest import seed_org_and_user


@pytest_asyncio.fixture(autouse=True)
async def _no_ptr(monkeypatch):
    from app.services.source_identification import service_identifier

    async def _none(ip):
        return None

    monkeypatch.setattr(service_identifier, "resolve_ptr", _none)


async def _mail(db, org, domain, ip, *, policy, disposition, count, days_ago, passes=False):
    now = datetime.now(timezone.utc)
    begin = now - timedelta(days=days_ago)
    report = DmarcAggregateReport(
        organization_id=org.id, domain_id=domain.id, report_id=str(uuid.uuid4()), org_name="google.com",
        date_range_begin=begin, date_range_end=begin + timedelta(days=1), policy_published_domain=domain.name,
        policy_p=policy, received_at=now, created_at=now,
    )
    db.add(report)
    await db.flush()
    result = AuthResult.pass_ if passes else AuthResult.fail
    db.add(DmarcAggregateRecord(
        organization_id=org.id, report_id=report.id, domain_id=domain.id, source_ip=ip, count=count,
        disposition=disposition, dkim_result=result, spf_result=result, header_from=domain.name,
        auth_results={}, created_at=now,
    ))


async def _breakdown(owner_factory, org, setup):
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified)
        db.add(domain)
        await db.flush()
        await setup(db, domain)
        await db.commit()
    async with owner_factory() as db:
        rows = await service_breakdown(db, domain.id)
        await db.rollback()
    return {r["service_label"]: r["likely_spoofed"] for r in rows}


async def test_mail_from_before_enforcement_doesnt_hide_spoofing(api):
    _client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)

    async def setup(db, domain):
        # Delivered while the domain was at p=none, then rejected once it enforced.
        await _mail(db, org, domain, "198.51.100.1", policy="none", disposition=Disposition.none, count=50, days_ago=150)
        await _mail(db, org, domain, "198.51.100.1", policy="reject", disposition=Disposition.reject, count=50, days_ago=20)

    assert await _breakdown(owner_factory, org, setup) == {"198.51.100.1": True}


async def test_only_ever_seen_under_p_none_is_not_judged(api):
    _client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)

    async def setup(db, domain):
        await _mail(db, org, domain, "198.51.100.2", policy="none", disposition=Disposition.none, count=50, days_ago=10)

    assert await _breakdown(owner_factory, org, setup) == {"198.51.100.2": False}


async def test_delivered_under_enforcement_or_authenticated_is_not_spoofed(api):
    _client, owner_factory = api
    org, _ = await seed_org_and_user(owner_factory)

    async def setup(db, domain):
        # Receivers let most of it through despite p=reject (forwarding, local overrides).
        await _mail(db, org, domain, "198.51.100.3", policy="reject", disposition=Disposition.none, count=40, days_ago=5)
        await _mail(db, org, domain, "198.51.100.3", policy="reject", disposition=Disposition.reject, count=10, days_ago=5)
        # Authenticated: never spoofed, whatever happened to it.
        await _mail(db, org, domain, "198.51.100.4", policy="reject", disposition=Disposition.reject, count=10, days_ago=5, passes=True)

    assert await _breakdown(owner_factory, org, setup) == {"198.51.100.3": False, "198.51.100.4": False}
