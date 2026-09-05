from app.models.domain import Domain

from tests.conftest import login_as, seed_org_and_user


async def test_action_queue_empty_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    assert response.json() == []


async def test_action_queue_scoped_to_one_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue", params={"domain_id": str(domain.id)})

    assert response.status_code == 200


async def test_action_queue_unknown_domain_404s(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue", params={"domain_id": "00000000-0000-0000-0000-000000000000"})

    assert response.status_code == 404
