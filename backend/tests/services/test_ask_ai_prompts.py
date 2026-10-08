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
            "spf": "v=spf1 include:spf.protection.outlook.com include:spf.smtp2go.com -all",
            "dmarc": "v=DMARC1; p=none; rua=mailto:reports+abc@hosted.example",
        }.get(kind)

    async def _mx(domain_name):
        return ["example-com.mail.protection.outlook.com"]

    monkeypatch.setattr(service_identifier, "resolve_ptr", _no_ptr)
    monkeypatch.setattr(prompts, "current_record", _record)
    monkeypatch.setattr(prompts, "mx_hosts", _mx)


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
                        "dkim": [{"selector": "s1", "domain": "esp.example", "result": "pass" if result == AuthResult.pass_ else "fail"}],
                    },
                )
            )
        await db.commit()
    return org, domain


async def _build(owner_factory, org, domain, kind, subject, issue=None):
    async with owner_factory() as db:
        await set_org_context(db, org.id)
        domain = await db.get(Domain, domain.id)
        text = await prompts.build_prompt(db, domain, kind, subject, issue)
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
    assert "v=spf1 include:spf.protection.outlook.com include:spf.smtp2go.com -all" in text
    assert "s1 (esp.example): 0 passed, 40 failed" in text
    assert "Reported by: google.com (40)" in text
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


async def test_prompts_say_the_issue_mail_setup_and_answer_structure(api):
    _client, owner_factory = api
    org, domain = await _seed(owner_factory)
    text = await _build(
        owner_factory, org, domain, "sender", "203.0.113.70",
        issue='example.com sender "203.0.113.70": 0.0% SPF / 0.0% DKIM. Contact admin@test.example',
    )

    assert 'The issue: example.com sender "203.0.113.70": 0.0% SPF / 0.0% DKIM.' in text
    assert "Mail setup: receives mail via Microsoft 365; SPF allows Microsoft 365, SMTP2GO." in text
    for part in ("1. The likely cause", "2. Step-by-step fixes", "3. How to check", "4. Risks"):
        assert part in text
    _assert_private_left_out(text)


async def test_mail_setup_follows_spf_redirect(monkeypatch):
    async def _record(kind, domain_name):
        return "v=spf1 redirect=_spf.google.com" if kind == "spf" else None

    async def _no_mx(domain_name):
        return []

    monkeypatch.setattr(prompts, "current_record", _record)
    monkeypatch.setattr(prompts, "mx_hosts", _no_mx)
    assert await prompts._mail_setup("example.com") == "Mail setup: SPF allows Google Workspace."


def test_microsofts_newer_mx_names_are_recognized():
    assert prompts._providers(["example-com.x-v1.mx.microsoft"]) == ["Microsoft 365"]


async def _review(owner_factory, org, domain, label, status):
    from app.models.sender_review import SenderReview

    async with owner_factory() as db:
        db.add(SenderReview(organization_id=org.id, domain_id=domain.id, service_label=label, status=status))
        await db.commit()


async def test_sender_prompt_states_the_verdict(api):
    from app.models.enums import SenderReviewStatus

    _client, owner_factory = api
    org, domain = await _seed(owner_factory)

    text = await _build(owner_factory, org, domain, "sender", "203.0.113.70")
    assert "My verdict: not reviewed yet." in text and "only if it is" in text

    await _review(owner_factory, org, domain, "203.0.113.70", SenderReviewStatus.blocked)
    text = await _build(owner_factory, org, domain, "sender", "203.0.113.70")
    assert "My verdict: blocked" in text and "Don't suggest making it pass" in text

    await _review(owner_factory, org, domain, "203.0.113.71", SenderReviewStatus.approved)
    text = await _build(owner_factory, org, domain, "sender", "203.0.113.71")
    assert "My verdict: approved" in text and "make it pass DMARC" in text


async def test_likely_spoofed_sender_is_not_to_be_fixed(api):
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    _client, owner_factory = api
    org, domain = await _seed(owner_factory)
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        report = DmarcAggregateReport(
            organization_id=org.id, domain_id=domain.id, report_id=str(_uuid.uuid4()), org_name="google.com",
            date_range_begin=now - timedelta(days=2), date_range_end=now - timedelta(days=1),
            policy_published_domain=domain.name, policy_p="reject", received_at=now, created_at=now,
        )
        db.add(report)
        await db.flush()
        db.add(DmarcAggregateRecord(
            organization_id=org.id, report_id=report.id, domain_id=domain.id, source_ip="198.51.100.66", count=30,
            disposition=Disposition.reject, dkim_result=AuthResult.fail, spf_result=AuthResult.fail,
            header_from=domain.name, created_at=now, auth_results={},
        ))
        await db.commit()

    text = await _build(owner_factory, org, domain, "sender", "198.51.100.66")
    assert "looks spoofed" in text and "don't suggest making it pass" in text.lower()

    compliance = await _build(owner_factory, org, domain, "compliance", None)
    assert '"198.51.100.66"' in compliance and "looks spoofed" in compliance
    assert "keep failing" in compliance


def test_message_counts_read_naturally():
    assert prompts._messages(1) == "1 message"
    assert prompts._messages(1200) == "1,200 messages"
