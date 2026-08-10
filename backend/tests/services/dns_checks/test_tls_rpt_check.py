"""check_tls_rpt_rua_destination — does a domain's TLS-RPT rua= actually
reach this org's configured mailbox? Mirrors dmarc_record.py's
check_rua_destination and its test coverage: a domain can publish a
syntactically valid TLS-RPT record while rua= points somewhere else
entirely (a stale vendor, a typo), a gap invisible to tls_rpt_check.py's
own presence/validity check by design.

Also covers check()'s own mailbox_address comparison — unlike DMARC's
checker, this one DOES surface the mismatch directly as a scored finding
(see the module's own docstring for why), not only via the builder."""

from unittest.mock import patch

from app.services.dns_checks import tls_rpt_check
from app.services.dns_checks.resolver import DnsLookupError
from app.services.dns_checks.tls_rpt_check import TlsRptRecordInfo, check_tls_rpt_rua_destination


def _fetch(record: TlsRptRecordInfo | None):
    async def fake_fetch_current_tls_rpt_record(domain):
        return record

    return patch("app.services.dns_checks.tls_rpt_check.fetch_current_tls_rpt_record", fake_fetch_current_tls_rpt_record)


def _record(rua: str) -> TlsRptRecordInfo:
    return TlsRptRecordInfo(raw=f"v=TLSRPTv1; rua={rua}", tags={"rua": rua})


async def test_correct_when_mailbox_is_among_targets():
    with _fetch(_record("mailto:reports@example.com")):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "correct"
    assert result.current_targets == ["reports@example.com"]


async def test_correct_is_case_insensitive():
    with _fetch(_record("mailto:Reports@Example.com")):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "correct"


async def test_points_elsewhere_when_mailbox_not_among_targets():
    with _fetch(_record("mailto:old-vendor@third-party.com")):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "points_elsewhere"
    assert result.current_targets == ["old-vendor@third-party.com"]


async def test_multiple_targets_correct_if_mailbox_is_one_of_them():
    with _fetch(_record("mailto:old-vendor@third-party.com,mailto:reports@example.com")):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "correct"


async def test_https_only_rua_counts_as_no_rua_for_mailbox_comparison():
    # An https: report endpoint isn't "the org's mailbox" — the check only
    # ever compares against mailto: targets, same as DMARC's.
    with _fetch(_record("https://reports.example.com/submit")):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "no_rua"
    assert result.current_targets == []


async def test_no_rua_when_record_has_no_rua_tag():
    with _fetch(TlsRptRecordInfo(raw="v=TLSRPTv1", tags={})):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "no_rua"


async def test_not_configured_when_no_record_at_all():
    with _fetch(None):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "not_configured"


async def test_lookup_error_when_dns_fails():
    async def fake(domain):
        raise DnsLookupError("timeout")

    with patch("app.services.dns_checks.tls_rpt_check.fetch_current_tls_rpt_record", fake):
        result = await check_tls_rpt_rua_destination("example.com", "reports@example.com")
    assert result.status == "lookup_error"


def _mocked_check(txt_records: list[str]):
    async def fake_resolve_mx(domain):
        return [(10, "mail.example.com")]

    async def fake_resolve_txt_strict(name):
        return txt_records

    return patch.multiple(
        "app.services.dns_checks.tls_rpt_check",
        resolve_mx=fake_resolve_mx,
        resolve_txt_strict=fake_resolve_txt_strict,
    )


async def test_check_flags_mailbox_mismatch_as_warn():
    with _mocked_check(["v=TLSRPTv1; rua=mailto:old-vendor@third-party.com"]):
        findings = await tls_rpt_check.check("example.com", mailbox_address="reports@example.com")
    mismatch = [f for f in findings if "doesn't include your configured mailbox" in f.summary]
    assert len(mismatch) == 1
    assert mismatch[0].status == "warn"
    # The existing presence/validity pass finding is untouched — this is an
    # additional finding, not a replacement.
    assert any(f.status == "pass" and "TLS-RPT reports configured" in f.summary for f in findings)


async def test_check_no_mismatch_finding_when_mailbox_matches():
    with _mocked_check(["v=TLSRPTv1; rua=mailto:reports@example.com"]):
        findings = await tls_rpt_check.check("example.com", mailbox_address="reports@example.com")
    assert not any("doesn't include your configured mailbox" in f.summary for f in findings)


async def test_check_no_mismatch_finding_when_no_mailbox_address_given():
    # Unaffected/unchanged behavior when the caller has no mailbox to
    # compare against (e.g. a local-auth org with no connection yet) —
    # mailbox_address defaults to None.
    with _mocked_check(["v=TLSRPTv1; rua=mailto:old-vendor@third-party.com"]):
        findings = await tls_rpt_check.check("example.com")
    assert not any("doesn't include your configured mailbox" in f.summary for f in findings)


async def test_check_flags_mismatch_for_https_only_rua():
    # A legitimate RFC 8460 config, but still means reports never reach
    # this app's own mailbox for this domain.
    with _mocked_check(["v=TLSRPTv1; rua=https://reports.example.com/submit"]):
        findings = await tls_rpt_check.check("example.com", mailbox_address="reports@example.com")
    assert any(f.status == "warn" and "doesn't include your configured mailbox" in f.summary for f in findings)


async def test_check_no_mismatch_finding_when_rua_itself_is_invalid():
    # Nothing "valid" to compare a mailbox against — the invalid-scheme
    # fail finding already covers this domain's real problem, no need for
    # a second, confusing finding about the mailbox on top of it.
    with _mocked_check(["v=TLSRPTv1; rua=ftp://not-a-real-scheme"]):
        findings = await tls_rpt_check.check("example.com", mailbox_address="reports@example.com")
    assert not any("doesn't include your configured mailbox" in f.summary for f in findings)
    assert any(f.status == "fail" for f in findings)
