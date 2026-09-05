from collections.abc import Sequence
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus


async def get_owned_domain(db: AsyncSession, domain_id: UUID, organization_id: UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return domain


async def list_domains_for_org(db: AsyncSession, organization_id: UUID) -> Sequence[Domain]:
    result = await db.execute(
        select(Domain).where(Domain.organization_id == organization_id).order_by(Domain.name)
    )
    return result.scalars().all()


async def count_domains_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Domain).where(Domain.organization_id == organization_id)
    )
    return result.scalar_one()


async def count_verified_domains_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Domain).where(
            Domain.organization_id == organization_id,
            Domain.verification_status == DomainVerificationStatus.verified,
        )
    )
    return result.scalar_one()


async def count_subdomains(db: AsyncSession, domain_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Domain).where(Domain.parent_domain_id == domain_id)
    )
    return result.scalar_one()
