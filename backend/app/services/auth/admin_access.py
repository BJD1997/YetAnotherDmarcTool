"""Can anyone sign in to the admin console? Checked at api startup, so a
fresh install without a platform admin says so in the log people actually
open (the migrate container that creates one runs once and stops)."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.repositories.platform_admin import count_platform_admins

NO_ADMIN = (
    "No platform admin exists yet, so nobody can sign in to the admin console (/admin). "
    "Set PLATFORM_ADMIN_BOOTSTRAP_EMAIL and PLATFORM_ADMIN_BOOTSTRAP_PASSWORD in the environment "
    "(the .env file, or the stack's environment variables in Portainer) and restart or redeploy: "
    "the migrate step then creates that account."
)


async def admin_access_problem(db: AsyncSession) -> str | None:
    """NO_ADMIN while there's no platform admin and no operator organization
    (whose org admins can also reach /admin); None otherwise."""
    if await count_platform_admins(db) > 0:
        return None
    operators = (await db.execute(select(func.count()).select_from(Organization).where(Organization.is_operator.is_(True)))).scalar_one()
    return None if operators else NO_ADMIN
