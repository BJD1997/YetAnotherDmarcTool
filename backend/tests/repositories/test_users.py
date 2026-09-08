from app.repositories.users import get_user_by_org_and_email

from tests.conftest import seed_org_and_user


async def test_get_user_by_org_and_email_found_and_not_found(api):
    _client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        found = await get_user_by_org_and_email(db, org.id, user.email)
        missing = await get_user_by_org_and_email(db, org.id, "nobody@example.com")
    assert found is not None
    assert found.id == user.id
    assert missing is None
