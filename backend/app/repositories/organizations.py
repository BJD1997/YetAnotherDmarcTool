from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization


async def get_organization(db: AsyncSession, organization_id: UUID) -> Organization | None:
    return await db.get(Organization, organization_id)
