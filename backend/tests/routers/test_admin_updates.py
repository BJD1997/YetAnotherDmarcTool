from app.config import settings
from app.services import update_check, updater_client
from tests.conftest import login_as_platform_admin


async def test_get_update_status_requires_admin(api):
    client, _owner_factory = api
    response = await client.get("/api/admin/updates")
    assert response.status_code == 401


async def test_get_update_status(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.get("/api/admin/updates")

    assert response.status_code == 200
    assert "running_version" in response.json()


async def test_update_settings(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    response = await client.patch("/api/admin/updates", json={"include_prereleases": True})

    assert response.status_code == 200
    assert response.json()["include_prereleases"] is True


async def test_status_says_how_this_deployment_updates(api, monkeypatch):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)

    monkeypatch.setattr(settings, "updater_url", None)
    monkeypatch.setattr(settings, "deployment_platform", "azure-container-apps")
    monkeypatch.setattr(settings, "azure_resource_group", "rg-yadt")
    body = (await client.get("/api/admin/updates")).json()
    assert (body["self_update_available"], body["deployment_platform"], body["azure_resource_group"]) == (
        False, "azure-container-apps", "rg-yadt",
    )

    monkeypatch.setattr(settings, "updater_url", "http://updater:9999")
    monkeypatch.setattr(settings, "updater_shared_secret", "s3cret")
    assert (await client.get("/api/admin/updates")).json()["self_update_available"] is True


async def test_triggering_records_the_requested_version_for_the_updater(api, monkeypatch):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    # update_check_state is one shared row the api fixture doesn't reset.
    async with owner_factory() as db:
        state = await update_check.get_or_create_state(db)
        state.requested_version = None
        state.requested_rehearsal = False
        state.latest_version = "v0.1.5-rc2"
        await db.commit()
    assert (await client.get("/api/update-request")).json() == {"version": None, "rehearsal": False}

    monkeypatch.setattr(settings, "app_version", "v0.1.5-rc1")
    triggered = []

    async def fake_trigger(version):
        triggered.append(version)

    monkeypatch.setattr(updater_client, "trigger_update", fake_trigger)

    assert (await client.post("/api/admin/updates/trigger")).status_code == 202
    assert triggered == ["v0.1.5-rc2"]
    # Readable without a session — the Azure updater job has none.
    client.cookies.clear()
    assert (await client.get("/api/update-request")).json() == {"version": "v0.1.5-rc2", "rehearsal": False}


async def test_test_update_rehearses_the_running_version_on_azure(api, monkeypatch):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    monkeypatch.setattr(settings, "app_version", "v0.1.5-rc3")
    triggered = []

    async def fake_trigger(version):
        triggered.append(version)

    monkeypatch.setattr(updater_client, "trigger_update", fake_trigger)

    # Not on Azure: refused.
    monkeypatch.setattr(settings, "azure_updater_job_id", None)
    assert (await client.post("/api/admin/updates/rehearse")).status_code == 409
    assert (await client.get("/api/admin/updates")).json()["rehearsal_available"] is False

    monkeypatch.setattr(settings, "azure_updater_job_id", "/subscriptions/s/resourceGroups/rg/providers/Microsoft.App/jobs/yadt-updater")
    monkeypatch.setattr(settings, "azure_update_client_id", "trigger-client")

    # On Azure but not switched on: hidden and refused.
    monkeypatch.setattr(settings, "update_rehearsal_enabled", False)
    assert (await client.get("/api/admin/updates")).json()["rehearsal_available"] is False
    assert (await client.post("/api/admin/updates/rehearse")).status_code == 409
    assert triggered == []

    monkeypatch.setattr(settings, "update_rehearsal_enabled", True)
    assert (await client.get("/api/admin/updates")).json()["rehearsal_available"] is True
    assert (await client.post("/api/admin/updates/rehearse")).status_code == 202
    assert triggered == ["v0.1.5-rc3"]
    assert (await client.get("/api/update-request")).json() == {"version": "v0.1.5-rc3", "rehearsal": True}
