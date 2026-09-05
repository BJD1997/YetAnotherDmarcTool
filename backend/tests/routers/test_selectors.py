from app.models.domain import Domain
from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_selectors_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/selectors")

    assert response.status_code == 200
    assert response.json() == []


async def test_create_selector(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    assert response.status_code == 201
    assert response.json()["selector"] == "google"


async def test_create_selector_rejects_invalid_format(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "not valid!"})

    assert response.status_code == 422


async def test_create_duplicate_selector_conflicts(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)
    await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    response = await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})

    assert response.status_code == 409


async def test_delete_selector(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)
    created = (await client.post(f"/api/domains/{domain.id}/selectors", json={"selector": "google"})).json()

    response = await client.delete(f"/api/domains/{domain.id}/selectors/{created['id']}")

    assert response.status_code == 204


async def test_detected_selectors_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/selectors/detected")

    assert response.status_code == 200
    assert response.json() == []
