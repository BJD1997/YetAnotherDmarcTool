from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.enums import AuthResult, Disposition, SenderReviewStatus
from app.models.sender_review import SenderReview


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


async def list_sender_reviews_for_domain(db: AsyncSession, domain_id: UUID) -> Sequence[SenderReview]:
    result = await db.execute(select(SenderReview).where(SenderReview.domain_id == domain_id))
    return result.scalars().all()


async def upsert_missing_sender_reviews(
    db: AsyncSession, organization_id: UUID, domain_id: UUID, service_labels: list[str]
) -> None:
    """Lazily creates a pending SenderReview row for every label in
    `service_labels` that doesn't already have one. ON CONFLICT DO NOTHING
    rather than get-then-insert — two orgs' (or two tabs') page-loads racing
    on the same (domain_id, service_label) shouldn't 500 on the unique
    constraint, same race-tolerant spirit as identify_many's cache upsert."""
    if not service_labels:
        return
    stmt = pg_insert(SenderReview).values(
        [
            {
                "id": uuid4(),
                "organization_id": organization_id,
                "domain_id": domain_id,
                "service_label": label,
                "status": SenderReviewStatus.pending.value,
            }
            for label in service_labels
        ]
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["domain_id", "service_label"])
    await db.execute(stmt)
    await db.flush()


async def get_sender_review(db: AsyncSession, domain_id: UUID, service_label: str) -> SenderReview | None:
    result = await db.execute(
        select(SenderReview).where(SenderReview.domain_id == domain_id, SenderReview.service_label == service_label)
    )
    return result.scalar_one_or_none()


async def create_sender_review(db: AsyncSession, *, organization_id: UUID, domain_id: UUID, service_label: str) -> SenderReview:
    review = SenderReview(organization_id=organization_id, domain_id=domain_id, service_label=service_label)
    db.add(review)
    return review


async def dmarc_trend_by_day(
    db: AsyncSession, organization_id: UUID, *, domain_id: UUID | None, since: datetime
) -> Sequence:
    """One row per calendar day: total volume, dmarc-pass volume, spf-aligned
    volume, dkim-aligned volume, rejected volume — the /dmarc/trend chart's
    entire data source in one grouped query."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    def _sum_where(condition):
        return func.coalesce(func.sum(case((condition, DmarcAggregateRecord.count), else_=0)), 0)

    day = func.date_trunc("day", DmarcAggregateReport.date_range_begin)
    query = (
        select(
            day.label("day"),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            _sum_where(dmarc_pass),
            _sum_where(DmarcAggregateRecord.spf_result == AuthResult.pass_),
            _sum_where(DmarcAggregateRecord.dkim_result == AuthResult.pass_),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.reject),
        )
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(
            DmarcAggregateRecord.organization_id == organization_id,
            DmarcAggregateReport.date_range_begin >= since,
        )
        .group_by(day)
        .order_by(day)
    )
    if domain_id is not None:
        query = query.where(DmarcAggregateRecord.domain_id == domain_id)
    return (await db.execute(query)).all()


async def failed_message_volume_for_org_since(
    db: AsyncSession, organization_id: UUID, since: datetime, *, domain_id: UUID | None = None
) -> int:
    """Distinct from failed_message_volume_for_domain above: that one is
    domain-scoped/all-time (used by the Domains list card); this is
    org-wide-or-domain-scoped AND date-windowed, for /dmarc/posture."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    query = select(func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)).where(
        DmarcAggregateRecord.organization_id == organization_id
    )
    if domain_id is not None:
        query = query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateRecord.domain_id == domain_id, DmarcAggregateReport.date_range_begin >= since)
    else:
        query = query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateReport.date_range_begin >= since)
    return (await db.execute(query)).scalar_one()


async def count_new_pending_senders_since(
    db: AsyncSession, organization_id: UUID, since: datetime, *, domain_id: UUID | None = None
) -> int:
    query = select(func.count()).select_from(SenderReview).where(
        SenderReview.organization_id == organization_id,
        SenderReview.status == SenderReviewStatus.pending,
        SenderReview.created_at >= since,
    )
    if domain_id is not None:
        query = query.where(SenderReview.domain_id == domain_id)
    return (await db.execute(query)).scalar_one()
