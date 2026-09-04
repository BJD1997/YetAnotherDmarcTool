import asyncio

from app.models.enums import SourceMatchMethod
from app.services.dns_checks.resolver import DnsLookupError
from app.services.source_identification import service_identifier as si


def _aret(value):
    async def _f(*args, **kwargs):
        return value

    return _f


def _araise(exc):
    async def _f(*args, **kwargs):
        raise exc

    return _f


async def test_forward_confirm_true_when_address_matches(monkeypatch):
    monkeypatch.setattr(si, "resolve_address", _aret(["203.0.113.5", "198.51.100.9"]))
    assert await si._forward_confirm("203.0.113.5", "mail.example.com") is True


async def test_forward_confirm_false_when_no_address_matches(monkeypatch):
    monkeypatch.setattr(si, "resolve_address", _aret(["198.51.100.9"]))
    assert await si._forward_confirm("203.0.113.5", "mail.example.com") is False


async def test_forward_confirm_false_when_hostname_has_no_forward_record(monkeypatch):
    # resolve_address returns [] on NXDOMAIN/NoAnswer — a definitive FCrDNS failure.
    monkeypatch.setattr(si, "resolve_address", _aret([]))
    assert await si._forward_confirm("203.0.113.5", "mail.example.com") is False


async def test_forward_confirm_none_on_lookup_error(monkeypatch):
    # A transient forward-lookup failure is "undetermined", not a failure — so it
    # isn't cached as False (see _forward_confirm docstring).
    monkeypatch.setattr(si, "resolve_address", _araise(DnsLookupError("boom")))
    assert await si._forward_confirm("203.0.113.5", "mail.example.com") is None


async def test_forward_confirm_normalizes_ipv6(monkeypatch):
    # A compressed source IP still matches an expanded forward AAAA record.
    monkeypatch.setattr(si, "resolve_address", _aret(["2001:0db8:0000:0000:0000:0000:0000:0001"]))
    assert await si._forward_confirm("2001:db8::1", "mail.example.com") is True


async def test_resolve_one_no_ptr_is_ip_fallback_with_null_fcrdns(monkeypatch):
    monkeypatch.setattr(si, "resolve_ptr", _aret(None))
    _ip, identity = await si._resolve_one("203.0.113.5", asyncio.Semaphore(1))
    assert identity.match_method == SourceMatchMethod.ip_fallback
    assert identity.service_label == "203.0.113.5"
    assert identity.fcrdns_valid is None  # nothing to forward-confirm


async def test_resolve_one_pattern_match_carries_fcrdns(monkeypatch):
    monkeypatch.setattr(si, "resolve_ptr", _aret("mail-0107.protection.outlook.com"))
    monkeypatch.setattr(si, "resolve_address", _aret(["203.0.113.5"]))
    monkeypatch.setattr(si, "match_known_service", lambda hostname: "Microsoft 365")
    _ip, identity = await si._resolve_one("203.0.113.5", asyncio.Semaphore(1))
    assert identity.match_method == SourceMatchMethod.pattern
    assert identity.service_label == "Microsoft 365"
    assert identity.fcrdns_valid is True


async def test_resolve_one_ptr_domain_records_failed_forward_confirm(monkeypatch):
    monkeypatch.setattr(si, "resolve_ptr", _aret("host.unknown-esp.example"))
    monkeypatch.setattr(si, "resolve_address", _aret(["198.51.100.9"]))  # doesn't point back
    monkeypatch.setattr(si, "match_known_service", lambda hostname: None)
    _ip, identity = await si._resolve_one("203.0.113.5", asyncio.Semaphore(1))
    assert identity.match_method == SourceMatchMethod.ptr_domain
    assert identity.fcrdns_valid is False
