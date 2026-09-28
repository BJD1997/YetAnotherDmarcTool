from app.config import settings
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
