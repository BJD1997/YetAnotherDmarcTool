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


async def test_lookup_count_finding_carries_the_record_and_what_each_part_costs():
    with _txt(
        {
            "example.com": ["v=spf1  ip4:192.0.2.1 include:spf.protection.outlook.com include:esp.example mx ~all"],
            "spf.protection.outlook.com": ["v=spf1 ip4:40.92.0.0/15 -all"],
            "esp.example": ["v=spf1 include:a.esp.example include:b.esp.example -all"],
            "a.esp.example": ["v=spf1 ip4:198.51.100.0/24 -all"],
            "b.esp.example": ["v=spf1 ip4:203.0.113.0/24 -all"],
        }
    ):
        findings = await spf.check("example.com")
    details = next(f for f in findings if "lookup" in f.summary).details
    assert details["record"] == "v=spf1 ip4:192.0.2.1 include:spf.protection.outlook.com include:esp.example mx ~all"
    assert details["lookup_count"] == 5
    assert details["terms"] == [
        {"term": "ip4:192.0.2.1", "lookups": 0, "service": None},
        {"term": "include:spf.protection.outlook.com", "lookups": 1, "service": "Microsoft 365"},
        {"term": "include:esp.example", "lookups": 3, "service": None},
        {"term": "mx", "lookups": 1, "service": None},
        {"term": "~all", "lookups": 0, "service": None},
    ]


async def test_redirect_is_listed_last_with_its_cost():
    with _txt({"example.com": ["v=spf1 redirect=_spf.google.com"], "_spf.google.com": ["v=spf1 include:_netblocks.google.com ~all"]}):
        findings = await spf.check("example.com")
    terms = next(f for f in findings if "lookup" in f.summary).details["terms"]
    assert terms == [{"term": "redirect=_spf.google.com", "lookups": 2, "service": "Google Workspace"}]


async def test_evaluate_counts_the_draft_not_the_published_record():
    with _txt(
        {
            "example.com": ["v=spf1 -all"],
            "icloud.com": ["v=spf1 include:a.icloud.example -all"],
            "a.icloud.example": ["v=spf1 ip4:17.0.0.0/8 -all"],
        }
    ):
        result = await spf.evaluate("example.com", "v=spf1 include:icloud.com include:missing.example -all")
    assert result["lookup_count"] == 3
    assert result["void_lookup_count"] == 1
    assert result["capped"] is False
    assert result["terms"][0] == {"term": "include:icloud.com", "lookups": 2, "service": "iCloud Mail"}


async def test_evaluate_stops_following_includes_past_the_cap():
    # Depth is already bounded (10), so the cap is about width: a record
    # with many includes, each of which would otherwise be fetched.
    records = {"wide.example": ["v=spf1 " + " ".join(f"include:w{i}.example" for i in range(60)) + " -all"]}
    records.update({f"w{i}.example": ["v=spf1 ip4:192.0.2.1 -all"] for i in range(60)})
    fetched: list[str] = []

    async def fake_resolve_txt_strict(name):
        fetched.append(name)
        return records.get(name, [])

    with patch("app.services.dns_checks.spf.resolve_txt_strict", fake_resolve_txt_strict):
        result = await spf.evaluate("example.com", "v=spf1 include:wide.example -all")
    assert result["capped"] is True
    assert result["lookup_count"] == 61
    assert len(fetched) <= spf.EVALUATE_LOOKUP_CAP + 1
