"""RFC 7489 DMARC checker — in particular the subdomain policy-inheritance
logic (_check_inherited_policy): a subdomain with no _dmarc record of its
own isn't automatically unprotected, it inherits the parent's sp=/p=. This
was a real bug fixed during development (subdomains were flagged as
"missing DMARC" even when a parent's p=reject already covered them)."""

from unittest.mock import patch

import pytest

from app.services.dns_checks import dmarc
from app.services.dns_checks.resolver import DnsLookupError


def _txt(records_by_name: dict[str, list[str]]):
    async def fake_resolve_txt_strict(name):
        if name not in records_by_name:
            raise AssertionError(f"unexpected lookup: {name}")
        return records_by_name[name]

    return patch("app.services.dns_checks.dmarc.resolve_txt_strict", fake_resolve_txt_strict)


def _statuses(findings):
    return [f.status for f in findings]


@pytest.mark.parametrize(
    "policy_line,expected_status",
    [
        ("v=DMARC1; p=reject; rua=mailto:reports@example.com;", "pass"),
        ("v=DMARC1; p=quarantine; rua=mailto:reports@example.com;", "warn"),
        ("v=DMARC1; p=none; rua=mailto:reports@example.com;", "warn"),
        ("v=DMARC1; rua=mailto:reports@example.com;", "fail"),  # missing p=
    ],
)
async def test_own_record_policy_strength(policy_line, expected_status):
    with _txt({"_dmarc.example.com": [policy_line]}):
        findings = await dmarc.check("example.com")
    policy_findings = [f for f in findings if f.summary.startswith("Policy is") or f.summary.startswith("Missing")]
    assert policy_findings[0].status == expected_status


async def test_multiple_records_is_a_fail():
    with _txt({"_dmarc.example.com": ["v=DMARC1; p=reject;", "v=DMARC1; p=none;"]}):
        findings = await dmarc.check("example.com")
    assert any(f.status == "fail" and "2 DMARC records" in f.summary for f in findings)


async def test_no_rua_is_a_warning():
    with _txt({"_dmarc.example.com": ["v=DMARC1; p=reject;"]}):
        findings = await dmarc.check("example.com")
    assert any(f.status == "warn" and "No rua=" in f.summary for f in findings)


# --- Subdomain inheritance (RFC 7489) ---


async def test_subdomain_inherits_reject_from_parent():
    with _txt({"_dmarc.sub.example.com": [], "_dmarc.example.com": ["v=DMARC1; p=reject;"]}):
        findings = await dmarc.check("sub.example.com", parent_domain="example.com")
    assert len(findings) == 1
    assert findings[0].status == "pass"
    assert "inherits p=reject" in findings[0].summary


async def test_subdomain_inherits_quarantine_as_warn():
    with _txt({"_dmarc.sub.example.com": [], "_dmarc.example.com": ["v=DMARC1; p=quarantine;"]}):
        findings = await dmarc.check("sub.example.com", parent_domain="example.com")
    assert findings[0].status == "warn"
    assert "inherits p=quarantine" in findings[0].summary


async def test_subdomain_inherits_none_as_warn_monitoring_only():
    with _txt({"_dmarc.sub.example.com": [], "_dmarc.example.com": ["v=DMARC1; p=none;"]}):
        findings = await dmarc.check("sub.example.com", parent_domain="example.com")
    assert findings[0].status == "warn"
    assert "monitoring only" in findings[0].summary


async def test_subdomain_prefers_explicit_sp_over_parent_p():
    with _txt({"_dmarc.sub.example.com": [], "_dmarc.example.com": ["v=DMARC1; p=none; sp=reject;"]}):
        findings = await dmarc.check("sub.example.com", parent_domain="example.com")
    assert findings[0].status == "pass"
    assert "inherits sp=reject" in findings[0].summary


async def test_subdomain_with_no_parent_record_either_is_unprotected():
    with _txt({"_dmarc.sub.example.com": [], "_dmarc.example.com": []}):
        findings = await dmarc.check("sub.example.com", parent_domain="example.com")
    assert findings[0].status == "fail"
    assert "nothing protects this subdomain" in findings[0].summary


async def test_subdomain_with_no_parent_domain_given_is_plain_missing():
    with _txt({"_dmarc.sub.example.com": []}):
        findings = await dmarc.check("sub.example.com", parent_domain=None)
    assert findings[0].status == "fail"
    assert findings[0].summary == "No DMARC record found"


async def test_own_record_takes_priority_over_parent_even_when_both_exist():
    with _txt({"_dmarc.sub.example.com": ["v=DMARC1; p=reject; rua=mailto:x@sub.example.com;"], "_dmarc.example.com": ["v=DMARC1; p=none;"]}):
        findings = await dmarc.check("sub.example.com", parent_domain="example.com")
    # should score its OWN record (reject), never touching the parent lookup
    assert any(f.status == "pass" and f.summary == "Policy is p=reject" for f in findings)


async def test_lookup_error_on_own_domain_is_error_not_fail():
    async def raise_error(name):
        raise DnsLookupError("timeout")

    with patch("app.services.dns_checks.dmarc.resolve_txt_strict", raise_error):
        findings = await dmarc.check("example.com")
    assert findings == [dmarc.Finding(status="error", summary="DMARC lookup failed: timeout")]


async def test_parent_lookup_error_is_error_not_fail():
    async def fake(name):
        if name == "_dmarc.sub.example.com":
            return []
        raise DnsLookupError("timeout")

    with patch("app.services.dns_checks.dmarc.resolve_txt_strict", fake):
        findings = await dmarc.check("sub.example.com", parent_domain="example.com")
    assert findings[0].status == "error"
    assert "couldn't check parent" in findings[0].summary
