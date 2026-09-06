from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus

from tests.conftest import login_as, seed_org_and_user


async def test_onboarding_status_fresh_org_local_auth(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body["has_domain"] is False
    assert body["has_verified_domain"] is False
    assert body["has_mailbox"] is True  # local-auth org: hosted mailbox always available


async def test_onboarding_status_fresh_org_entra(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/onboarding/status")

    assert response.status_code == 200
    assert response.json()["has_mailbox"] is False  # entra org, no MailboxConnection yet


async def test_onboarding_status_with_verified_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified))
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body["has_domain"] is True
    assert body["has_verified_domain"] is True
