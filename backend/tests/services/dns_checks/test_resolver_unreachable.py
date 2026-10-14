"""An unreachable resolver container is a failed lookup, not a crash: the
notifications job and sender identification skip reverse DNS instead of
failing for the whole organization."""

import asyncio

import pytest

from app.config import settings
from app.services.dns_checks import resolver
from app.services.dns_checks.resolver import DnsLookupError
from app.services.source_identification import service_identifier


@pytest.fixture
def unreachable_resolver(monkeypatch):
    monkeypatch.setattr(settings, "dns_resolver_host", "resolver.invalid")
    monkeypatch.setattr(resolver, "_resolver", None)


async def test_lookups_raise_dns_lookup_error(unreachable_resolver):
    with pytest.raises(DnsLookupError, match="not reachable"):
        await resolver.resolve_ptr("192.0.2.1")
    assert await resolver.resolve_txt("example.com") == []
    assert resolver._resolver is None  # not cached: the next lookup retries


async def test_sender_identification_falls_back_to_the_ip(unreachable_resolver, monkeypatch):
    monkeypatch.setattr(service_identifier, "resolve_ptr", resolver.resolve_ptr)
    ip, identity = await service_identifier._resolve_one("192.0.2.1", asyncio.Semaphore(1))

    assert ip == "192.0.2.1"
    assert identity.service_label == "192.0.2.1"
