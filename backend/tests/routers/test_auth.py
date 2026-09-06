from tests.conftest import login_as, seed_org_and_user


async def test_auth_config_sso_disabled(api):
    client, _owner_factory = api
    response = await client.get("/api/auth/config")
    assert response.status_code == 200
    assert response.json() == {"entra_sso_enabled": False}


async def test_login_redirect_404_when_sso_disabled(api):
    client, _owner_factory = api
    response = await client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 404


async def test_me_requires_auth(api):
    client, _owner_factory = api
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


async def test_me_returns_current_user(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == user.email
    assert body["organization_id"] == str(org.id)


async def test_logout_clears_session(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    logout_response = await client.post("/api/auth/logout")
    assert logout_response.status_code == 204

    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 401
