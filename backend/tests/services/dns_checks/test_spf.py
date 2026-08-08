"""SPF checker — the RFC 7208 structural checks (lookup/void-lookup limits)
plus the conditional ~all/-all mode: it should only prefer ~all once DMARC
is actually enforcing (p=quarantine/reject) for a domain that sends mail,
never for a parked/receive_only domain or one with no real enforcement yet."""

from unittest.mock import patch

import pytest

from app.models.enums import DomainMailProfile, SpfAllQualifierMode
from app.services.dns_checks import spf


def _txt(records_by_name: dict[str, list[str]]):
    async def fake_resolve_txt_strict(name):
        return records_by_name.get(name, [])

    return patch("app.services.dns_checks.spf.resolve_txt_strict", fake_resolve_txt_strict)


def _all_finding(findings):
    return next(f for f in findings if "all" in f.summary.lower() and "mechanism" not in f.summary)


async def test_no_record_is_a_fail():
    with _txt({}):
        findings = await spf.check("example.com")
    assert findings == [
        spf.Finding(
            status="fail",
            summary="No SPF record found",
            details={"recommendation": 'Publish a TXT record — e.g. "v=spf1 -all" if this domain sends no mail.'},
        )
    ]


async def test_strict_mode_always_prefers_hardfail_even_with_dmarc_enforcing():
    with _txt({"example.com": ["v=spf1 -all"]}):
        findings = await spf.check(
            "example.com", DomainMailProfile.sends_mail, "reject", SpfAllQualifierMode.strict
        )
    assert _all_finding(findings).status == "pass"


async def test_conditional_mode_warns_on_hardfail_when_dmarc_is_reject():
    with _txt({"example.com": ["v=spf1 -all"]}):
        findings = await spf.check(
            "example.com", DomainMailProfile.sends_mail, "reject", SpfAllQualifierMode.conditional
        )
    finding = _all_finding(findings)
    assert finding.status == "warn"
    assert "~all is recommended instead" in finding.summary


async def test_conditional_mode_passes_softfail_when_dmarc_is_reject():
    with _txt({"example.com": ["v=spf1 ~all"]}):
        findings = await spf.check(
            "example.com", DomainMailProfile.sends_mail, "reject", SpfAllQualifierMode.conditional
        )
    finding = _all_finding(findings)
    assert finding.status == "pass"
    assert "appropriate since DMARC is already enforcing" in finding.summary


async def test_conditional_mode_passes_softfail_when_dmarc_is_quarantine():
    with _txt({"example.com": ["v=spf1 ~all"]}):
        findings = await spf.check(
            "example.com", DomainMailProfile.sends_mail, "quarantine", SpfAllQualifierMode.conditional
        )
    assert _all_finding(findings).status == "pass"


@pytest.mark.parametrize("dmarc_policy", [None, "none"])
async def test_conditional_mode_stays_strict_when_dmarc_not_enforcing(dmarc_policy):
    with _txt({"example.com": ["v=spf1 ~all"]}):
        findings = await spf.check(
            "example.com", DomainMailProfile.sends_mail, dmarc_policy, SpfAllQualifierMode.conditional
        )
    finding = _all_finding(findings)
    assert finding.status == "warn"
    assert "consider -all" in finding.summary


async def test_conditional_mode_ignores_reject_for_non_sending_domain():
    # a parked/receive_only domain sends no legitimate mail at all — DMARC's
    # own enforcement is irrelevant to what SPF's all-qualifier should be here.
    with _txt({"example.com": ["v=spf1 -all"]}):
        findings = await spf.check(
            "example.com", DomainMailProfile.receive_only, "reject", SpfAllQualifierMode.conditional
        )
    assert _all_finding(findings).status == "pass"


async def test_too_many_lookups_is_a_fail():
    records = {"example.com": ["v=spf1 " + " ".join(f"include:s{i}.example.com" for i in range(11)) + " -all"]}
    for i in range(11):
        records[f"s{i}.example.com"] = ["v=spf1 -all"]
    with _txt(records):
        findings = await spf.check("example.com")
    assert any(f.status == "fail" and "exceeds the RFC 7208 10-lookup limit" in f.summary for f in findings)


async def test_multiple_records_is_a_fail():
    with _txt({"example.com": ["v=spf1 -all", "v=spf1 ~all"]}):
        findings = await spf.check("example.com")
    assert any(f.status == "fail" and "2 SPF records" in f.summary for f in findings)


async def test_missing_all_mechanism_warns():
    with _txt({"example.com": ["v=spf1 include:_spf.example.net"], "_spf.example.net": ["v=spf1 -all"]}):
        findings = await spf.check("example.com")
    assert any(f.status == "warn" and "weak implicit ?all" in f.summary for f in findings)
