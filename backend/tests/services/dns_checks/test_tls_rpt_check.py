"""check_tls_rpt_rua_destination — does a domain's TLS-RPT rua= actually
reach this org's configured mailbox? Mirrors dmarc_record.py's
check_rua_destination and its test coverage: a domain can publish a
syntactically valid TLS-RPT record while rua= points somewhere else
entirely (a stale vendor, a typo), a gap invisible to tls_rpt_check.py's
own presence/validity check by design."""

from unittest.mock import patch

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
