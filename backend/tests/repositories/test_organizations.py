from app.models.enums import OrganizationStatus
from app.models.organization import Organization
from app.repositories.organizations import get_org_by_demo_flag

from tests.conftest import seed_org_and_user


async def test_get_org_by_demo_flag_returns_none_when_no_demo_org(api):
    _client, owner_factory = api
    await seed_org_and_user(owner_factory)

    async with owner_factory() as db:
        result = await get_org_by_demo_flag(db)

    assert result is None


async def test_get_org_by_demo_flag_finds_the_demo_org(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        org = Organization(name="Demo", status=OrganizationStatus.active, is_demo_read_only=True)
        db.add(org)
        await db.commit()
        await db.refresh(org)

    async with owner_factory() as db:
        result = await get_org_by_demo_flag(db)

    assert result is not None
    assert result.id == org.id
