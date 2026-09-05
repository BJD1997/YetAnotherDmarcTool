from datetime import datetime, timezone

from app.models.enums import AuthMethod, SignInResult, UserRole
from app.models.sign_in_event import SignInEvent

from tests.conftest import login_as, seed_org_and_user


async def test_list_sign_in_events_requires_admin(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/sign-in-events")

    assert response.status_code == 403


async def test_list_sign_in_events(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        db.add(
            SignInEvent(
                organization_id=org.id, attempted_email="a@example.com", auth_method=AuthMethod.local,
                result=SignInResult.success, created_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/sign-in-events")

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["email"] == "a@example.com"
    assert body["has_more"] is False


async def test_list_sign_in_events_filters_by_result(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        db.add(
            SignInEvent(
                organization_id=org.id, attempted_email="ok@example.com", auth_method=AuthMethod.local,
                result=SignInResult.success, created_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            SignInEvent(
                organization_id=org.id, attempted_email="bad@example.com", auth_method=AuthMethod.local,
                result=SignInResult.failure, created_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/sign-in-events", params={"result": "failure"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["email"] == "bad@example.com"
