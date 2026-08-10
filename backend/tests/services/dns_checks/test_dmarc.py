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


# --- rua=/ruf= vs. the org's own configured mailbox ---


async def test_rua_mismatch_is_a_warn():
    with _txt({"_dmarc.example.com": ["v=DMARC1; p=reject; rua=mailto:old-vendor@example.com;"]}):
        findings = await dmarc.check("example.com", mailbox_address="reports@example.com")
    assert any(f.status == "warn" and "rua= doesn't include your configured mailbox" in f.summary for f in findings)
    # The existing presence finding is untouched — additional, not replaced.
    assert any(f.status == "pass" and "Aggregate reports (rua) configured" in f.summary for f in findings)


async def test_rua_match_has_no_mismatch_finding():
    with _txt({"_dmarc.example.com": ["v=DMARC1; p=reject; rua=mailto:reports@example.com;"]}):
        findings = await dmarc.check("example.com", mailbox_address="reports@example.com")
    assert not any("doesn't include your configured mailbox" in f.summary for f in findings)


async def test_no_mailbox_address_given_means_no_mismatch_finding():
    with _txt({"_dmarc.example.com": ["v=DMARC1; p=reject; rua=mailto:old-vendor@example.com;"]}):
        findings = await dmarc.check("example.com")
    assert not any("doesn't include your configured mailbox" in f.summary for f in findings)


async def test_ruf_mismatch_is_a_warn():
    # Same domain as the record itself, so this doesn't also trigger the
    # unrelated RFC 7489 §7.1 external-authorization lookup — that's
    # covered by its own tests, not what's under test here.
    with _txt({"_dmarc.example.com": ["v=DMARC1; p=reject; rua=mailto:reports@example.com; ruf=mailto:forensics@example.com;"]}):
        findings = await dmarc.check("example.com", mailbox_address="reports@example.com")
    assert any(f.status == "warn" and "ruf= doesn't include your configured mailbox" in f.summary for f in findings)


async def test_ruf_not_configured_means_no_mismatch_finding():
    # ruf= is optional and its absence isn't flagged at all — nothing to
    # compare a mailbox against when it was never configured.
    with _txt({"_dmarc.example.com": ["v=DMARC1; p=reject; rua=mailto:reports@example.com;"]}):
        findings = await dmarc.check("example.com", mailbox_address="reports@example.com")
    assert not any("ruf=" in f.summary and "doesn't include" in f.summary for f in findings)
