from collections.abc import Sequence
from datetime import datetime
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


async def get_domain_id_by_org_and_name(db: AsyncSession, organization_id: UUID, name: str) -> UUID | None:
    result = await db.execute(select(Domain.id).where(Domain.organization_id == organization_id, Domain.name == name))
    return result.scalar_one_or_none()


async def get_domain_by_org_and_name(db: AsyncSession, organization_id: UUID, name: str) -> Domain | None:
    result = await db.execute(select(Domain).where(Domain.organization_id == organization_id, Domain.name == name))
    return result.scalar_one_or_none()


async def list_domains_with_hosted_report_address(db: AsyncSession) -> Sequence[Domain]:
    result = await db.execute(select(Domain).where(Domain.hosted_report_address.is_not(None)))
    return result.scalars().all()


async def list_pending_domains(db: AsyncSession) -> Sequence[Domain]:
    result = await db.execute(select(Domain).where(Domain.verification_status == DomainVerificationStatus.pending))
    return result.scalars().all()


async def mark_pending_subdomains_verified(db: AsyncSession, parent_domain_id: UUID, verified_at: datetime) -> None:
    """Propagates verification to any still-pending subdomains of an apex
    that just got verified — see apply_domain_verification in
    app/services/dns_checks/domain_verification.py."""
    await db.execute(
        Domain.__table__.update()
        .where(Domain.parent_domain_id == parent_domain_id, Domain.verification_status == DomainVerificationStatus.pending)
        .values(verification_status=DomainVerificationStatus.verified, verified_at=verified_at)
    )
