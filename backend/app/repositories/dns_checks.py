from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dns_check import DnsCheckResult
from app.models.enums import CheckType
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


async def list_dns_check_results_at_latest_run(db: AsyncSession, domain_id: UUID) -> dict[CheckType, list[DnsCheckResult]]:
    """Every DnsCheckResult row from the domain's most recent recheck,
    grouped by check_type. Used by domain_rating.py's compute_domain_rating
    (via latest_findings_by_type) and app/routers/domains.py directly."""
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at)).where(DnsCheckResult.domain_id == domain_id).scalar_subquery()
    )
    check_rows = (
        (
            await db.execute(
                select(DnsCheckResult).where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.checked_at == latest_ts)
            )
        )
        .scalars()
        .all()
    )
    findings_by_type: dict[CheckType, list[DnsCheckResult]] = {}
    for row in check_rows:
        findings_by_type.setdefault(row.check_type, []).append(row)
    return findings_by_type


async def latest_dns_check_results_of_type_for_domain(
    db: AsyncSession, domain_id: UUID, check_type: CheckType
) -> Sequence[DnsCheckResult]:
    """Directly surfaces the existing finding for one check_type from the
    last check run — used by action_queue/rules.py's spf_lookup_limit_risk.
    Deliberately narrower than list_dns_check_results_at_latest_run (which
    returns every check_type): that function's other caller
    (domain_rating.py, and via it app/routers/domains.py) genuinely needs
    every type at once, but this caller only ever wants one, so a separate
    narrow query avoids fetching and discarding unrelated check types on
    every action-queue evaluation (which runs per-domain, potentially in a
    loop over every domain in an org)."""
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at))
        .where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.check_type == check_type)
        .scalar_subquery()
    )
    result = await db.execute(
        select(DnsCheckResult).where(
            DnsCheckResult.domain_id == domain_id,
            DnsCheckResult.check_type == check_type,
            DnsCheckResult.checked_at == latest_ts,
        )
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
