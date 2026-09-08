import hashlib

from app.config import settings
from app.repositories.auth import get_platform_admin_session_by_token_hash, get_user_session_by_token_hash
from app.services.auth import session_manager

from tests.conftest import login_as_platform_admin, seed_org_and_user


async def test_get_user_session_by_token_hash_round_trips(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)

    async with owner_factory() as db:
        _session, raw_token = await session_manager.create_user_session(
            db, user_id=user.id, organization_id=org.id, ip_address="127.0.0.1", user_agent="pytest",
        )
        await db.commit()

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    async with owner_factory() as db:
        found = await get_user_session_by_token_hash(db, token_hash)
    assert found is not None
    assert found.user_id == user.id


async def test_get_user_session_by_token_hash_returns_none_for_unknown_hash(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        result = await get_user_session_by_token_hash(db, "a" * 64)
    assert result is None


async def test_get_platform_admin_session_by_token_hash_round_trips(api):
    client, owner_factory = api
    await login_as_platform_admin(client, owner_factory)
    raw_token = client.cookies.get(settings.platform_admin_session_cookie_name)
    assert raw_token is not None

    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    async with owner_factory() as db:
        found = await get_platform_admin_session_by_token_hash(db, token_hash)
    assert found is not None


async def test_get_platform_admin_session_by_token_hash_returns_none_for_unknown_hash(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        result = await get_platform_admin_session_by_token_hash(db, "b" * 64)
    assert result is None
