import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain


async def match_domain(db: AsyncSession, organization_id: uuid.UUID, published_domain: str) -> uuid.UUID | None:
    """Resolves a report's policy_published/reported domain to a registered
    Domain row: exact match first, then walk up to the closest registered
    ancestor (e.g. a report published for "mail.sub.example.com" matches a
    registered apex "example.com" even though the exact subdomain wasn't
    separately added). Returns None if nothing matches — the caller persists
    the report with domain_id=NULL into the "unmatched" bucket rather than
    dropping it."""
    published_domain = published_domain.strip().lower().rstrip(".")

    labels = published_domain.split(".")
    for start in range(len(labels) - 1):
        candidate = ".".join(labels[start:])
        result = await db.execute(
            select(Domain.id).where(Domain.organization_id == organization_id, Domain.name == candidate)
        )
        domain_id = result.scalar_one_or_none()
        if domain_id is not None:
            return domain_id

    return None
