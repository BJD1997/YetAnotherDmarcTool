from app.repositories.admin_updates import get_or_create_update_check_state


async def test_get_or_create_update_check_state_creates_once(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        first = await get_or_create_update_check_state(db)
        await db.commit()
        first_id = first.id

    async with owner_factory() as db:
        second = await get_or_create_update_check_state(db)
    assert second.id == first_id
