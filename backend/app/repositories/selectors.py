from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dkim_selector import DkimSelector
from app.models.domain import Domain


async def list_selectors_for_domain(db: AsyncSession, domain_id: UUID) -> Sequence[DkimSelector]:
    result = await db.execute(
        select(DkimSelector).where(DkimSelector.domain_id == domain_id).order_by(DkimSelector.selector)
    )
    return result.scalars().all()


async def known_selector_names(db: AsyncSession, domain_id: UUID) -> set[str]:
    result = await db.execute(select(DkimSelector.selector).where(DkimSelector.domain_id == domain_id))
    return set(result.scalars().all())


async def known_selector_names_for_org(db: AsyncSession, organization_id: UUID) -> set[tuple[UUID, str]]:
    result = await db.execute(
        select(DkimSelector.domain_id, DkimSelector.selector)
        .join(Domain, Domain.id == DkimSelector.domain_id)
        .where(Domain.organization_id == organization_id)
    )
    return {(domain_id, selector) for domain_id, selector in result.all()}
