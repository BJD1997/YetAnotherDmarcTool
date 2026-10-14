"""Report mailboxes are public, so oversized messages and reports that
unpack to something huge are refused before they eat the worker's memory."""

import gzip

import httpx
import parsedmarc
import pytest

from app.services.graph import mailbox_poller
from app.services.ingestion import parsedmarc_adapter


def _serve(monkeypatch, body: bytes):
    async def _token(_tenant_id):
        return "token"

    real_client = httpx.AsyncClient
    monkeypatch.setattr(mailbox_poller, "get_graph_token", _token)
    monkeypatch.setattr(
        mailbox_poller.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)), **kwargs),
    )


async def test_oversized_message_is_not_downloaded(monkeypatch):
    monkeypatch.setattr(mailbox_poller, "MAX_MESSAGE_BYTES", 1024)
    _serve(monkeypatch, b"x" * 4096)

    with pytest.raises(mailbox_poller.MessageTooLargeError):
        await mailbox_poller.fetch_message_raw_mime(tenant_id="t", mailbox="m@example.com", message_id="1")


async def test_normal_message_is_downloaded(monkeypatch):
    _serve(monkeypatch, b"x" * 4096)

    assert await mailbox_poller.fetch_message_raw_mime(tenant_id="t", mailbox="m@example.com", message_id="1") == b"x" * 4096


def test_report_that_unpacks_past_25_mb_is_refused():
    assert parsedmarc.MAX_DECOMPRESSED_REPORT_SIZE == parsedmarc_adapter.MAX_DECOMPRESSED_REPORT_BYTES == 25 * 1024 * 1024
    bomb = gzip.compress(b"<feedback>" + b" " * (26 * 1024 * 1024))

    with pytest.raises(parsedmarc.ParserError, match="limit"):
        parsedmarc.extract_report(bomb)
