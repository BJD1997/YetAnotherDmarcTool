"""DANE/TLSA checker — RFC 7672 requires the resolver to have DNSSEC-
validated the TLSA answer before it can be trusted at all (an unvalidated
answer could have been injected off-path). Verified against RFC 7672
directly before writing these: a "secure" DNSSEC status is a MUST for the
TLSA RRset to be actionable; anything else must not be relied on."""

from unittest.mock import patch

from app.services.dns_checks import dane


def _mocked(mx_records, tlsa_by_host: dict[str, tuple[list, bool]]):
    async def fake_resolve_mx(domain):
        return mx_records

    async def fake_resolve_tlsa(name):
        host = name.removeprefix("_25._tcp.")
        return tlsa_by_host.get(host, ([], False))

    return patch.multiple(
        "app.services.dns_checks.dane",
        resolve_mx=fake_resolve_mx,
        resolve_tlsa=fake_resolve_tlsa,
    )


_VALID_RECORD = (3, 1, 1, "a" * 64)  # usage=3 (DANE-EE), selector=1 (SPKI), mtype=1 (SHA-256, 64 hex chars)


async def test_dnssec_validated_tlsa_passes():
    with _mocked([(10, "mail.example.com")], {"mail.example.com": ([_VALID_RECORD], True)}):
        findings = await dane.check("example.com")
    assert len(findings) == 1
    assert findings[0].status == "pass"
    assert "DNSSEC-validated" in findings[0].summary


async def test_unvalidated_tlsa_is_a_hard_fail_not_a_warning():
    # RFC 7672: an unvalidated TLSA answer MUST NOT be trusted, even though
    # records were technically returned — this is the crux of the RFC
    # requirement the user asked me to verify.
    with _mocked([(10, "mail.example.com")], {"mail.example.com": ([_VALID_RECORD], False)}):
        findings = await dane.check("example.com")
    assert findings[0].status == "fail"
    assert "did NOT DNSSEC-validate" in findings[0].summary


async def test_no_tlsa_record_is_a_warning_not_a_fail():
    with _mocked([(10, "mail.example.com")], {"mail.example.com": ([], False)}):
        findings = await dane.check("example.com")
    assert findings[0].status == "warn"
    assert "DANE not deployed" in findings[0].summary


async def test_malformed_tlsa_is_a_fail():
    bad_record = (9, 1, 1, "a" * 64)  # usage=9 is invalid (valid set is 0-3)
    with _mocked([(10, "mail.example.com")], {"mail.example.com": ([bad_record], True)}):
        findings = await dane.check("example.com")
    assert findings[0].status == "fail"
    assert "malformed" in findings[0].summary


async def test_no_mx_means_nothing_to_check():
    with _mocked([], {}):
        findings = await dane.check("example.com")
    assert findings == []


async def test_null_mx_means_nothing_to_check():
    with _mocked([(0, "")], {}):
        findings = await dane.check("example.com")
    assert findings == []


async def test_checks_every_mx_host_independently():
    with _mocked(
        [(10, "mx1.example.com"), (20, "mx2.example.com")],
        {
            "mx1.example.com": ([_VALID_RECORD], True),
            "mx2.example.com": ([], False),
        },
    ):
        findings = await dane.check("example.com")
    statuses = {f.subject: f.status for f in findings}
    assert statuses == {"mx1.example.com": "pass", "mx2.example.com": "warn"}
