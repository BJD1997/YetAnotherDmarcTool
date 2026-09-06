from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dns_check import DnsCheckResult


async def count_dns_checks_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(DnsCheckResult).where(DnsCheckResult.organization_id == organization_id)
    )
    return result.scalar_one()


async def list_latest_check_results(db: AsyncSession, domain_id: UUID) -> Sequence[DnsCheckResult]:
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at))
        .where(DnsCheckResult.domain_id == domain_id)
        .scalar_subquery()
    )
    # NOT distinct-on (check_type, subject): a single check_type routinely
    # produces several findings sharing the same subject (SPF's lookup-count
    # finding and its 'all'-qualifier finding both have subject=NULL, same
    # for DMARC's several structural notes) — DISTINCT ON would silently
    # collapse those down to one row each. Every row from one recheck() call
    # shares the exact same checked_at (set once per call, see
    # recheck_domain in app/routers/dns_checks.py), so "the latest run's
    # results" is simply every row at the max checked_at for this domain.
    result = await db.execute(
        select(DnsCheckResult)
        .where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.checked_at == latest_ts)
        .order_by(DnsCheckResult.check_type, DnsCheckResult.subject.nulls_first())
    )
    return result.scalars().all()
