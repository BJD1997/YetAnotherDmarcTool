import uuid

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus, UserRole

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org, *, name: str = "example.com", **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name=name, **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_domains_empty(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/domains")

    assert response.status_code == 200
    assert response.json() == []


async def test_create_domain(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/domains", json={"name": "example.com"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "example.com"
    assert body["verification_status"] == "pending"


async def test_create_domain_rejects_invalid_name(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    response = await client.post("/api/domains", json={"name": "not a domain"})

    assert response.status_code == 422


async def test_get_domain_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_get_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}")

    assert response.status_code == 200
    assert response.json()["name"] == "example.com"


async def test_update_domain_requires_admin(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/domains/{domain.id}", json={"notes": "hi"})

    assert response.status_code == 403


async def test_update_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.patch(f"/api/domains/{domain.id}", json={"notes": "hi", "is_active": False})

    assert response.status_code == 200
    body = response.json()
    assert body["notes"] == "hi"
    assert body["is_active"] is False


async def test_delete_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.delete(f"/api/domains/{domain.id}")

    assert response.status_code == 204


async def test_delete_domain_blocked_by_subdomain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    parent = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)
    await _add_domain(owner_factory, org, name="sub.example.com", parent_domain_id=parent.id)
    await login_as(client, owner_factory, user)

    response = await client.delete(f"/api/domains/{parent.id}")

    assert response.status_code == 409


async def test_ranked_domains_includes_unverified_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/domains/ranked")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["not_verified"] is True
