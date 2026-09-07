from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dns_check import DnsCheckResult
from app.models.tls_rpt import TlsRptReport


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


async def list_tls_rpt_reports_for_domain(
    db: AsyncSession,
    domain_id: UUID,
    *,
    since: datetime | None,
    org_name: str | None,
    failures_only: bool,
) -> Sequence[TlsRptReport]:
    """Shared query for all three /dmarc/tls-rpt/* endpoints in
    app/routers/dns_checks.py — result_type filtering happens in Python
    there, not here, since failure_details is a JSONB array and this
    domain's real data volume (hundreds of rows over years, not raw
    message counts) doesn't justify jsonb_array_elements. Returns
    newest-first."""
    query = select(TlsRptReport).where(TlsRptReport.domain_id == domain_id)
    if since is not None:
        query = query.where(TlsRptReport.date_range_begin >= since)
    if org_name is not None:
        query = query.where(TlsRptReport.org_name.ilike(f"%{org_name}%"))
    if failures_only:
        query = query.where(TlsRptReport.summary_failure_count > 0)
    query = query.order_by(TlsRptReport.date_range_begin.desc())
    result = await db.execute(query)
    return result.scalars().all()
