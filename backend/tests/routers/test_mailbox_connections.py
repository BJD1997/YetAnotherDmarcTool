from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_get_mailbox_connection_not_found(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/mailbox-connection")

    assert response.status_code == 404


async def test_set_mailbox_connection_requires_entra_tenant(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    assert response.status_code == 409


async def test_set_mailbox_connection(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["mailbox_address"] == "reports@example.com"
    assert body["consent_status"] == "granted"


async def test_get_mailbox_connection_after_set(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)
    await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    response = await client.get("/api/mailbox-connection")

    assert response.status_code == 200
    assert response.json()["mailbox_address"] == "reports@example.com"


async def test_mailbox_job_runs_empty(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/mailbox-connection/job-runs")

    assert response.status_code == 200
    assert response.json() == []
