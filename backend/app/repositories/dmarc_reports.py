from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.enums import AuthResult


async def count_reports_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(
            DmarcAggregateReport.organization_id == organization_id
        )
    )
    return result.scalar_one()


async def count_reports_for_domain(db: AsyncSession, domain_id: UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.domain_id == domain_id)
    )
    return result.scalar_one()


async def last_report_received_at_for_domain(db: AsyncSession, domain_id: UUID) -> datetime | None:
    result = await db.execute(
        select(func.max(DmarcAggregateReport.received_at)).where(DmarcAggregateReport.domain_id == domain_id)
    )
    return result.scalar_one_or_none()


async def last_report_received_at_for_org(db: AsyncSession, organization_id: UUID) -> datetime | None:
    result = await db.execute(
        select(func.max(DmarcAggregateReport.received_at)).where(
            DmarcAggregateReport.organization_id == organization_id
        )
    )
    return result.scalar_one_or_none()


async def failed_message_volume_for_domain(db: AsyncSession, domain_id: UUID) -> int:
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    result = await db.execute(
        select(func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)).where(
            DmarcAggregateRecord.domain_id == domain_id
        )
    )
    return result.scalar_one()


async def list_auth_results_for_domain(db: AsyncSession, domain_id: UUID) -> Sequence[tuple]:
    result = await db.execute(
        select(DmarcAggregateRecord.auth_results, DmarcAggregateRecord.report_id, DmarcAggregateRecord.count).where(
            DmarcAggregateRecord.domain_id == domain_id
        )
    )
    return result.all()


async def dmarc_summary_totals(db: AsyncSession, domain_id: UUID) -> tuple[int, int]:
    """(total_message_count, dmarc_pass_count) for one domain, all-time. A
    message passes DMARC if EITHER SPF or DKIM is aligned-pass (RFC 7489) —
    policy_evaluated.{dkim,spf} in the aggregate report already reflect the
    receiver's own alignment-aware judgement, so no separate "alignment"
    bookkeeping is needed beyond what's already stored."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    total_count, pass_count = (
        await db.execute(
            select(
                func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
                func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
            ).where(DmarcAggregateRecord.domain_id == domain_id)
        )
    ).one()
    return int(total_count), int(pass_count)


async def dmarc_disposition_breakdown(db: AsyncSession, domain_id: UUID) -> dict[str, int]:
    rows = await db.execute(
        select(DmarcAggregateRecord.disposition, func.sum(DmarcAggregateRecord.count))
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.disposition)
    )
    return {disposition.value: count for disposition, count in rows.all()}


async def latest_published_policy_for_domain(db: AsyncSession, domain_id: UUID) -> str | None:
    return (
        await db.execute(
            select(DmarcAggregateReport.policy_p)
            .where(DmarcAggregateReport.domain_id == domain_id)
            .order_by(DmarcAggregateReport.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
