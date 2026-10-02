"""Ask AI prompts: the facts behind one issue, and never anything private —
no organization name, user email, mailbox or reporting address, raw report."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.db.rls import set_org_context
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import AuthResult, CheckStatus, CheckType, Disposition, DomainVerificationStatus
from app.services.ask_ai import prompts

from tests.conftest import seed_org_and_user

PRIVATE = ("Test Org", "admin@test.example", "reports+abc@hosted.example", "reports@yetanotherdmarctool.com")


@pytest_asyncio.fixture(autouse=True)
async def _offline(monkeypatch):
    from app.services.source_identification import service_identifier

    async def _no_ptr(ip):
        return None

    async def _record(kind, domain_name):
        return {
            "spf": "v=spf1 include:_spf.example.net -all",
            "dmarc": "v=DMARC1; p=none; rua=mailto:reports+abc@hosted.example",
        }.get(kind)

    monkeypatch.setattr(service_identifier, "resolve_ptr", _no_ptr)
    monkeypatch.setattr(prompts, "current_record", _record)


async def _seed(owner_factory):
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        domain = Domain(
            organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified,
            hosted_report_address="reports+abc@hosted.example",
        )
        db.add(domain)
        await db.flush()
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.dmarc, status=CheckStatus.warn,
                summary="rua= points to reports@yetanotherdmarctool.com on a different domain, but no authorization record",
                details={"recommendation": "Add the external authorization record."}, checked_at=now,
            )
        )
        report = DmarcAggregateReport(
            organization_id=org.id, domain_id=domain.id, report_id=str(uuid.uuid4()), org_name="google.com",
            date_range_begin=now - timedelta(days=2), date_range_end=now - timedelta(days=1),
            policy_published_domain=domain.name, policy_p="none", received_at=now, created_at=now,
        )
        db.add(report)
        await db.flush()
        for ip, count, result in (("203.0.113.70", 40, AuthResult.fail), ("203.0.113.71", 60, AuthResult.pass_)):
            db.add(
                DmarcAggregateRecord(
                    organization_id=org.id, report_id=report.id, domain_id=domain.id, source_ip=ip, count=count,
                    disposition=Disposition.none, dkim_result=result, spf_result=result, header_from=domain.name,
                    created_at=now,
                    auth_results={
                        "spf": [{"scope": "mfrom", "domain": "bounce.esp.example", "result": "pass"}],
                        "dkim": [{"selector": "s1", "domain": "esp.example", "result": "pass"}],
                    },
                )
            )
        await db.commit()
    return org, domain


async def _build(owner_factory, org, domain, kind, subject):
    async with owner_factory() as db:
        await set_org_context(db, org.id)
        domain = await db.get(Domain, domain.id)
        text = await prompts.build_prompt(db, domain, kind, subject)
        await db.rollback()
    return text


def _assert_private_left_out(text: str) -> None:
    for secret in PRIVATE:
        assert secret not in text, secret
    assert "@" not in text.replace("<your reporting address>", "")


async def test_dns_check_prompt(api):
    _client, owner_factory = api
    org, domain = await _seed(owner_factory)
    text = await _build(owner_factory, org, domain, "dns_check", "dmarc")

    assert "example.com" in text and "DMARC" in text
    assert "no authorization record" in text and "Add the external authorization record." in text
    assert "v=DMARC1; p=none; rua=mailto:<your reporting address>" in text
    _assert_private_left_out(text)


async def test_sender_prompt(api):
    _client, owner_factory = api
    org, domain = await _seed(owner_factory)
    text = await _build(owner_factory, org, domain, "sender", "203.0.113.70")

    assert "203.0.113.70" in text and "40 messages" in text
    assert "0.0% SPF aligned" in text and "0.0% DKIM aligned" in text
    assert "bounce.esp.example" in text and "esp.example" in text
    assert "v=spf1 include:_spf.example.net -all" in text
    _assert_private_left_out(text)


async def test_compliance_prompt(api):
    _client, owner_factory = api
    org, domain = await _seed(owner_factory)
    text = await _build(owner_factory, org, domain, "compliance", None)

    assert "60.0%" in text and "100 messages" in text
    assert "p=none" in text and "203.0.113.70" in text
    _assert_private_left_out(text)


async def test_unknown_kind_and_sender_are_refused(api):
    _client, owner_factory = api
    org, domain = await _seed(owner_factory)
    with pytest.raises(ValueError):
        await _build(owner_factory, org, domain, "everything", None)
    with pytest.raises(ValueError):
        await _build(owner_factory, org, domain, "sender", "nobody.example")


def test_long_prompts_are_capped():
    text = prompts.cap("x" * 10_000)
    assert len(text) <= prompts.MAX_PROMPT_CHARS
    assert "Shortened to fit" in text


def test_redact():
    assert prompts.redact("rua=mailto:a.b+c@x.example, mailto:d@e.nl") == (
        "rua=mailto:<your reporting address>, mailto:<your reporting address>"
    )
