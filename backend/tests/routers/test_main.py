from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_health_endpoint(api):
    client, _owner_factory = api
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_security_headers_present(api):
    client, _owner_factory = api
    response = await client.get("/api/health")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_csrf_header_required_for_post(api):
    client, _owner_factory = api
    response = await client.post("/api/auth/logout", headers={"X-Requested-With": ""})
    assert response.status_code == 403


async def test_demo_read_only_blocks_mutation(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, is_demo_read_only=True)
    await login_as(client, owner_factory, user)

    response = await client.patch("/api/organizations/current", json={"name": "Renamed"})

    assert response.status_code == 403
    assert "read-only" in response.json()["detail"]
