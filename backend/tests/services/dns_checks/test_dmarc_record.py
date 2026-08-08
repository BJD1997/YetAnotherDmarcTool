"""resolve_effective_policy — used by registry.py to decide whether SPF's
conditional ~all/-all preference applies. Must fall back to the parent's
sp=/p= for a subdomain with no DMARC record of its own, same inheritance
RFC 7489 gives the DMARC checker itself — this was a real bug: a subdomain
never got credited for conditional-mode ~all because this specifically
only checked the subdomain's own (usually absent) record."""

from unittest.mock import patch

from app.services.dns_checks.dmarc_record import DmarcRecordInfo, resolve_effective_policy
from app.services.dns_checks.resolver import DnsLookupError


def _fetch(records_by_domain: dict[str, DmarcRecordInfo | None]):
    async def fake_fetch_current_dmarc_record(domain):
        if domain not in records_by_domain:
            raise AssertionError(f"unexpected fetch: {domain}")
        return records_by_domain[domain]

    return patch("app.services.dns_checks.dmarc_record.fetch_current_dmarc_record", fake_fetch_current_dmarc_record)


def _record(p=None, sp=None):
    tags = {}
    if p:
        tags["p"] = p
    if sp:
        tags["sp"] = sp
    return DmarcRecordInfo(raw="v=DMARC1; " + "; ".join(f"{k}={v}" for k, v in tags.items()), tags=tags)


async def test_own_record_used_when_present():
    with _fetch({"sub.example.com": _record(p="quarantine")}):
        policy = await resolve_effective_policy("sub.example.com", "example.com")
    assert policy == "quarantine"


async def test_falls_back_to_parent_p_when_own_record_absent():
    with _fetch({"sub.example.com": None, "example.com": _record(p="reject")}):
        policy = await resolve_effective_policy("sub.example.com", "example.com")
    assert policy == "reject"


async def test_falls_back_to_parent_sp_over_p():
    with _fetch({"sub.example.com": None, "example.com": _record(p="none", sp="reject")}):
        policy = await resolve_effective_policy("sub.example.com", "example.com")
    assert policy == "reject"


async def test_none_when_no_parent_given_and_no_own_record():
    with _fetch({"sub.example.com": None}):
        policy = await resolve_effective_policy("sub.example.com", None)
    assert policy is None


async def test_none_when_neither_has_a_record():
    with _fetch({"sub.example.com": None, "example.com": None}):
        policy = await resolve_effective_policy("sub.example.com", "example.com")
    assert policy is None


async def test_lookup_failure_on_own_domain_falls_back_to_parent():
    # a failed lookup and a confirmed-absent record both mean "can't credit
    # enforcement from this domain's own record" — same treatment, by design
    # (see the function's own docstring for why this differs from dmarc.py's
    # checker, which reports these two cases differently to the user).
    async def fake(domain):
        if domain == "sub.example.com":
            raise DnsLookupError("timeout")
        return _record(p="reject")

    with patch("app.services.dns_checks.dmarc_record.fetch_current_dmarc_record", fake):
        policy = await resolve_effective_policy("sub.example.com", "example.com")
    assert policy == "reject"


async def test_lookup_failure_on_parent_yields_none():
    async def fake(domain):
        if domain == "sub.example.com":
            return None
        raise DnsLookupError("timeout")

    with patch("app.services.dns_checks.dmarc_record.fetch_current_dmarc_record", fake):
        policy = await resolve_effective_policy("sub.example.com", "example.com")
    assert policy is None
