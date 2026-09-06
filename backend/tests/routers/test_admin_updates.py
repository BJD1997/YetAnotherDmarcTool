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
