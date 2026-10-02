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


JOB_ID = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.App/jobs/yadt-updater"


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setattr(settings, "azure_updater_job_id", JOB_ID)
    monkeypatch.setattr(settings, "azure_update_client_id", "trigger-client-id")
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://identity.local/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", "identity-secret")


async def test_azure_only_starts_the_updater_job(monkeypatch, azure):
    """No overrides: the api's identity may start the job, not change what it
    runs. The job reads the requested version back itself."""
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.host == "identity.local":
            assert request.url.params["client_id"] == "trigger-client-id"
            assert request.headers["X-IDENTITY-HEADER"] == "identity-secret"
            return httpx.Response(200, json={"access_token": "arm-token"})
        assert request.headers["Authorization"] == "Bearer arm-token"
        return httpx.Response(202, json={"name": "yadt-updater-abc"})

    _use_transport(monkeypatch, handler)

    await updater_client.trigger_update("v0.1.5-rc2")

    arm_calls = [(r.method, r.url.path, r.content) for r in seen if r.url.host == "management.azure.com"]
    assert arm_calls == [("POST", f"{JOB_ID}/start", b"")]


async def test_azure_refusal_is_reported(monkeypatch, azure):
    def handler(request):
        if request.url.host == "identity.local":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(403, text="AuthorizationFailed")

    _use_transport(monkeypatch, handler)

    with pytest.raises(updater_client.UpdaterUnavailableError, match="Azure refused to start the updater for v0.1.5-rc2 \\(403\\)"):
        await updater_client.trigger_update("v0.1.5-rc2")
