"""Run once by the `migrate` one-off container after `alembic upgrade head`.
Creates the first platform_admins row from PLATFORM_ADMIN_BOOTSTRAP_EMAIL/
PASSWORD if the table is empty; a no-op once any admin already exists (so
these env vars can be left set across restarts without recreating anything)."""

import asyncio
import logging

from app.config import settings
from app.db.session import async_session_factory
from app.models.platform_admin import PlatformAdmin
from app.repositories.platform_admin import count_platform_admins
from app.services.auth.admin_access import admin_access_problem
from app.services.auth.password import hash_password
from app.services.setup_checks import example_database_login_problem

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bootstrap_platform_admin")


async def main() -> None:
    # Runs with the database owner's connection, the one password the api
    # never sees: warn here if it's still the example default.
    db_login_problem = example_database_login_problem(settings.database_url)
    if db_login_problem:
        logger.warning("SETUP PROBLEM: %s", db_login_problem)
    async with async_session_factory() as db:
        existing_count = await count_platform_admins(db)
        if existing_count > 0:
            logger.info("platform_admins already has %d row(s); skipping bootstrap", existing_count)
            return

        if not settings.platform_admin_bootstrap_email or not settings.platform_admin_bootstrap_password:
            problem = await admin_access_problem(db)
            if problem:
                logger.warning("SETUP PROBLEM: %s", problem)
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
