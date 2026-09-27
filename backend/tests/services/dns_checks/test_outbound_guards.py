"""The two connections this app makes to customer-controlled hosts: the
STARTTLS probe of a domain's MX hosts and the MTA-STS policy fetch."""

import asyncio

import httpx
import pytest

from app.services.dns_checks import mta_sts, starttls


def _resolves_to(monkeypatch, address: str):
    async def fake_getaddrinfo(host, port, **_kwargs):
        return [(None, None, None, "", (address, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize("address", ["10.21.0.5", "127.0.0.1", "169.254.169.254", "172.28.0.3", "::1"])
async def test_starttls_probe_refuses_internal_mx_hosts(monkeypatch, address):
    _resolves_to(monkeypatch, address)
    connected = []

    async def fake_open_connection(*args, **kwargs):
        connected.append(args)
        raise AssertionError("must not connect")

    monkeypatch.setattr(starttls.asyncio, "open_connection", fake_open_connection)

    finding = await starttls._probe_host("mx.attacker.example")

    assert finding.status == "error"
    assert "non-public" in finding.summary
    assert connected == []


async def test_starttls_probe_connects_to_the_checked_address(monkeypatch):
    _resolves_to(monkeypatch, "8.8.8.8")
    seen = []

    async def fake_open_connection(host, port):
        seen.append((host, port))
        raise OSError("stop here")

    monkeypatch.setattr(starttls.asyncio, "open_connection", fake_open_connection)

    await starttls._probe_host("mx.example.com")

    # The address already vetted, not the hostname (which could re-resolve).
    assert seen == [("8.8.8.8", 25)]


def _serve(monkeypatch, body: bytes, status: int = 200):
    async def allow(_host):
        return None

    monkeypatch.setattr(mta_sts, "assert_public_host", allow)
    transport = httpx.MockTransport(lambda request: httpx.Response(status, content=body))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(mta_sts.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw))


async def test_policy_fetch_reads_a_normal_policy(monkeypatch):
    _serve(monkeypatch, b"version: STSv1\nmode: enforce\nmx: mail.example.com\nmax_age: 604800\n")

    result = await mta_sts.fetch_policy_file("example.com")

    assert result.error is None
    assert "mode: enforce" in result.body


async def test_policy_fetch_refuses_an_oversized_body(monkeypatch):
    _serve(monkeypatch, b"x" * (mta_sts.MAX_POLICY_BYTES + 1))

    result = await mta_sts.fetch_policy_file("example.com")

    assert result.body is None
    assert "larger than 64 KB" in result.error


async def test_policy_fetch_reports_http_errors(monkeypatch):
    _serve(monkeypatch, b"nope", status=404)

    result = await mta_sts.fetch_policy_file("example.com")

    assert result.error == "MTA-STS policy fetch returned HTTP 404"

