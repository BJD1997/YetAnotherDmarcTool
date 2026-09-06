from app.models.enums import AuthMethod, UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_list_users(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/users")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["email"] == user.email


async def test_create_local_user_requires_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com"})

    assert response.status_code == 403


async def test_create_local_user_rejects_entra_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com"})

    assert response.status_code == 409


async def test_create_local_user(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/users", json={"email": "new@example.com", "display_name": "New Person"})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert "setup_link" in body


async def test_update_user_cannot_self_demote(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/users/{user.id}", json={"role": "member"})

    assert response.status_code == 400


async def test_update_user_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    # entra=True on the discarded second user avoids colliding with the
    # partial unique index on lower(email) for auth_method='local' users —
    # seed_org_and_user hardcodes the same email for every call (see Task 4).
    _other_org, other_user = await seed_org_and_user(owner_factory, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/users/{other_user.id}", json={"role": "org_admin"})

    assert response.status_code == 404
