"""The api's startup warning when nobody can sign in to the admin console."""

from sqlalchemy import text

from app.models.enums import OrganizationStatus
from app.models.organization import Organization
from app.models.platform_admin import PlatformAdmin
from app.services.auth.admin_access import admin_access_problem


async def test_warns_only_while_nobody_can_reach_the_admin_console(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        await db.execute(text("DELETE FROM platform_admins"))
        await db.commit()

    async with owner_factory() as db:
        problem = await admin_access_problem(db)
    assert "PLATFORM_ADMIN_BOOTSTRAP_EMAIL" in problem and "PLATFORM_ADMIN_BOOTSTRAP_PASSWORD" in problem

    async with owner_factory() as db:
        db.add(Organization(name="Operators", status=OrganizationStatus.active, is_operator=True))
        await db.commit()
    async with owner_factory() as db:
        assert await admin_access_problem(db) is None
        await db.execute(text("UPDATE organizations SET is_operator = false"))
        db.add(PlatformAdmin(email="admin@platform.example", password_hash="x", is_active=True))
        await db.commit()
    async with owner_factory() as db:
        assert await admin_access_problem(db) is None
        await db.execute(text("DELETE FROM platform_admins WHERE email = 'admin@platform.example'"))
        await db.commit()
