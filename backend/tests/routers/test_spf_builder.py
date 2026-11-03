"""SPF record builder: the published record it starts from, the services it
offers, which approved senders it suggests adding, and counting a draft."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, SenderReviewStatus, SourceMatchMethod
from app.models.sender_review import SenderReview
from app.models.source_ip_identity import SourceIpIdentity
from app.routers import dns_checks as dns_checks_router
from app.services.dns_checks import spf

from tests.conftest import login_as, seed_org_and_user

DNS = {
    "example.com": ["MS=ms123", "v=spf1  include:spf.protection.outlook.com ~all"],
    "spf.protection.outlook.com": ["v=spf1 ip4:40.92.0.0/15 -all"],
    "spf.smtp2go.com": ["v=spf1 ip4:203.0.113.0/24 -all"],
}


@pytest_asyncio.fixture(autouse=True)
async def _dns(monkeypatch):
    async def fake_resolve_txt_strict(name):
        return DNS.get(name, [])

    monkeypatch.setattr(dns_checks_router, "resolve_txt_strict", fake_resolve_txt_strict)
    monkeypatch.setattr(spf, "resolve_txt_strict", fake_resolve_txt_strict)


async def _seed(owner_factory, senders):
    """`senders`: (ip, service label, envelope-from domain, review status or None)."""
    org, user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        report = DmarcAggregateReport(
            organization_id=org.id, domain_id=domain.id, report_id=str(uuid.uuid4()), org_name="google.com",
            date_range_begin=now - timedelta(days=2), date_range_end=now - timedelta(days=1),
            policy_published_domain=domain.name, policy_p="none", received_at=now, created_at=now,
        )
        db.add(report)
        await db.flush()
        for ip, label, envelope_from, review in senders:
            await db.merge(
                SourceIpIdentity(source_ip=ip, service_label=label, match_method=SourceMatchMethod.ptr_domain, resolved_at=now)
            )
            db.add(
                DmarcAggregateRecord(
                    organization_id=org.id, report_id=report.id, domain_id=domain.id, source_ip=ip, count=12,
                    disposition=Disposition.none, dkim_result=AuthResult.fail, spf_result=AuthResult.fail,
                    header_from=domain.name, created_at=now,
                    auth_results={"spf": [{"scope": "mfrom", "domain": envelope_from, "result": "fail"}]},
                )
            )
            if review is not None:
                db.add(SenderReview(organization_id=org.id, domain_id=domain.id, service_label=label, status=review))
        await db.commit()
    return user, domain


async def test_builder_starts_from_the_published_record_and_lists_services(api):
    client, owner_factory = api
    user, domain = await _seed(owner_factory, [])
    await login_as(client, owner_factory, user)

    data = (await client.get(f"/api/domains/{domain.id}/dns/spf-builder")).json()

    assert data["current_record"] == "v=spf1 include:spf.protection.outlook.com ~all"
    assert data["current_record_count"] == 1
    assert data["current_record_lookup_error"] is False
    assert {"key": "microsoft365", "label": "Microsoft 365", "include": "spf.protection.outlook.com"} in data["providers"]
    assert data["suggestions"] == []


async def test_suggests_approved_senders_that_use_the_domain_as_envelope_from(api):
    client, owner_factory = api
    user, domain = await _seed(
        owner_factory,
        [
            # Approved, bounces through example.com itself: its include helps.
            ("198.51.100.230", "SMTP2GO", "example.com", SenderReviewStatus.approved),
            # Approved, but bounces through its own domain: an include on
            # example.com wouldn't be checked for this mail.
            ("198.51.100.231", "SendGrid", "bounces.sendgrid.net", SenderReviewStatus.approved),
            # Uses example.com, but nobody approved it.
            ("198.51.100.232", "Mailgun", "example.com", None),
            # Already in the record.
            ("198.51.100.233", "Microsoft 365", "example.com", SenderReviewStatus.approved),
        ],
    )
    await login_as(client, owner_factory, user)

    data = (await client.get(f"/api/domains/{domain.id}/dns/spf-builder")).json()

    assert data["suggestions"] == [{"provider_key": "smtp2go", "sender_label": "SMTP2GO", "messages": 12}]


async def test_evaluate_counts_a_draft(api):
    client, owner_factory = api
    user, domain = await _seed(owner_factory, [])
    await login_as(client, owner_factory, user)

    resp = await client.post(
        f"/api/domains/{domain.id}/dns/spf-builder/evaluate",
        json={"record": "v=spf1 include:spf.protection.outlook.com include:spf.smtp2go.com mx ~all"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["lookup_count"] == 3 and data["limit"] == 10
    assert [t["service"] for t in data["terms"]] == ["Microsoft 365", "SMTP2GO", None, None]


async def test_evaluate_refuses_what_isnt_an_spf_record(api):
    client, owner_factory = api
    user, domain = await _seed(owner_factory, [])
    await login_as(client, owner_factory, user)
    url = f"/api/domains/{domain.id}/dns/spf-builder/evaluate"

    for record in ("include:spf.example ~all", "v=spf1 " + "a " * 50 + "-all", "v=spf1 include:é.example -all", "v=spf1 " + "x" * 1100):
        assert (await client.post(url, json={"record": record})).status_code == 422, record
