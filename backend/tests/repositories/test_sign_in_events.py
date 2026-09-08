from app.repositories.sign_in_events import count_sign_in_events_for_org

from tests.conftest import seed_org_and_user


async def test_count_sign_in_events_for_org(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        before = await count_sign_in_events_for_org(db, org.id)
    assert before == 0
