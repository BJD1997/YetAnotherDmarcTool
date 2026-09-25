import httpx
import pytest

from app.config import settings
from app.services import updater_client


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "updater_url", "http://updater:9999")
    monkeypatch.setattr(settings, "updater_shared_secret", "secret")


def _use_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        updater_client.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )


async def test_passes_the_updaters_refusal_reason_through(monkeypatch):
    _use_transport(monkeypatch, lambda request: httpx.Response(409, json={"error": "v0.1.4 is not newer than the running v0.1.5"}))

    with pytest.raises(updater_client.UpdaterUnavailableError, match="not newer than the running"):
        await updater_client.trigger_update("v0.1.4")


async def test_unreachable_updater_is_reported_not_raised_raw(monkeypatch):
    def _refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    _use_transport(monkeypatch, _refuse)

    with pytest.raises(updater_client.UpdaterUnavailableError, match="couldn't reach the updater"):
        await updater_client.trigger_update("v0.1.6")


async def test_accepted_update_returns_normally(monkeypatch):
    _use_transport(monkeypatch, lambda request: httpx.Response(202, json={"status": "started"}))

    await updater_client.trigger_update("v0.1.6")
