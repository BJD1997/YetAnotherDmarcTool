"""Run once by the `migrate` one-off container after `alembic upgrade head`.
Creates the first platform_admins row from PLATFORM_ADMIN_BOOTSTRAP_EMAIL/
PASSWORD if the table is empty; a no-op once any admin already exists (so
these env vars can be left set across restarts without recreating anything)."""

import asyncio
import logging

from sqlalchemy import func, select

from app.config import settings
from app.db.session import async_session_factory
from app.models.platform_admin import PlatformAdmin
from app.services.auth.password import hash_password

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bootstrap_platform_admin")


async def main() -> None:
    async with async_session_factory() as db:
        result = await db.execute(select(func.count()).select_from(PlatformAdmin))
        existing_count = result.scalar_one()
        if existing_count > 0:
            logger.info("platform_admins already has %d row(s); skipping bootstrap", existing_count)
            return

        if not settings.platform_admin_bootstrap_email or not settings.platform_admin_bootstrap_password:
            logger.warning(
                "no platform_admins exist yet and PLATFORM_ADMIN_BOOTSTRAP_EMAIL/"
                "PASSWORD are not set — nobody will be able to log in to /admin "
                "until one is created."
            )
            return

        admin = PlatformAdmin(
            email=settings.platform_admin_bootstrap_email,
            password_hash=hash_password(settings.platform_admin_bootstrap_password),
        )
        db.add(admin)
        await db.commit()
        logger.info("created initial platform admin: %s", settings.platform_admin_bootstrap_email)


if __name__ == "__main__":
    asyncio.run(main())
