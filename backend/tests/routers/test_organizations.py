from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_get_current_organization(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/organizations/current")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(org.id)
    assert body["name"] == org.name
    assert body["status"] == "active"


async def test_get_current_organization_requires_auth(api):
    client, _owner_factory = api
    response = await client.get("/api/organizations/current")
    assert response.status_code == 401


async def test_update_current_organization_as_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.patch("/api/organizations/current", json={"name": "Renamed Org"})

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Org"


async def test_update_current_organization_requires_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    response = await client.patch("/api/organizations/current", json={"name": "Renamed Org"})

    assert response.status_code == 403
