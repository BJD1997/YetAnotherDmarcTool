from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization


async def get_organization(db: AsyncSession, organization_id: UUID) -> Organization | None:
    return await db.get(Organization, organization_id)


async def get_org_by_demo_flag(db: AsyncSession) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.is_demo_read_only.is_(True)))
    return result.scalar_one_or_none()
