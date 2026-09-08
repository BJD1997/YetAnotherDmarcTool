from sqlalchemy import text

from app.models.platform_admin import PlatformAdmin
from app.repositories.platform_admin import count_platform_admins
from app.services.auth.password import hash_password


async def test_count_platform_admins_zero_initially(api):
    _client, owner_factory = api
    # platform_admins table is not truncated by the fixture (see
    # test_platform_admin.py's test_admin_enroll_otp_flow comment), so we
    # manually truncate it for this test.
    async with owner_factory() as db:
        await db.execute(text("TRUNCATE platform_admins CASCADE"))
        await db.commit()
        result = await count_platform_admins(db)
    assert result == 0


async def test_count_platform_admins_increments(api):
    _client, owner_factory = api
    # Manual truncate for isolation.
    async with owner_factory() as db:
        await db.execute(text("TRUNCATE platform_admins CASCADE"))
        await db.commit()

    # Get initial count.
    async with owner_factory() as db:
        initial_count = await count_platform_admins(db)

    # Add an admin.
    async with owner_factory() as db:
        admin = PlatformAdmin(
            email="test-admin@platform.example",
            password_hash=hash_password("test_password"),
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        new_count = await count_platform_admins(db)

    assert new_count == initial_count + 1
