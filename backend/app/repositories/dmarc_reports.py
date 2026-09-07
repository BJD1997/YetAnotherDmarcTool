from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import case, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dismissed_detected_domain import DismissedDetectedDomain
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, SenderReviewStatus
from app.models.sender_review import SenderReview
from app.models.source_ip_identity import SourceIpIdentity
from app.models.tls_rpt import TlsRptReport


def _apply_report_filters(
    query,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
):
    """Shared WHERE-clause vocabulary for the reports/by-day, /summary and
    /grouped endpoints, applied to a query already joined to both
    DmarcAggregateReport and DmarcAggregateRecord."""
    if since is not None:
        query = query.where(DmarcAggregateReport.date_range_begin >= since)
    if disposition is not None:
        query = query.where(DmarcAggregateRecord.disposition == disposition)
    if spf_result is not None:
        query = query.where(DmarcAggregateRecord.spf_result == spf_result)
    if dkim_result is not None:
        query = query.where(DmarcAggregateRecord.dkim_result == dkim_result)
    if reporter is not None:
        query = query.where(DmarcAggregateReport.org_name.ilike(f"%{reporter}%"))
    if source_ip is not None:
        query = query.where(func.host(DmarcAggregateRecord.source_ip) == source_ip)
    return query


async def list_report_records_by_day(
    db: AsyncSession,
    domain_id: UUID,
    *,
    limit: int,
    before_id: UUID | None,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
) -> Sequence:
    """Row granularity is one DmarcAggregateRecord (one sending host within
    one report), not one whole report — a report with several source IPs
    shows as several rows on its day. Keyset-paginated on
    (date_range_begin, record id) rather than offset, so pages stay stable
    as new reports keep arriving between requests. Filters apply to the
    keyset query itself, not after the fact, since a busy domain can have
    thousands of records."""
    query = (
        select(
            DmarcAggregateRecord.id,
            DmarcAggregateReport.id.label("report_pk"),
            DmarcAggregateReport.org_name,
            DmarcAggregateReport.date_range_begin,
            DmarcAggregateRecord.source_ip,
            DmarcAggregateRecord.count,
            DmarcAggregateRecord.disposition,
            DmarcAggregateRecord.spf_result,
            DmarcAggregateRecord.dkim_result,
        )
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )

    if before_id is not None:
        anchor = (
            await db.execute(
                select(DmarcAggregateReport.date_range_begin, DmarcAggregateRecord.id)
                .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
                .where(DmarcAggregateRecord.id == before_id, DmarcAggregateRecord.domain_id == domain_id)
            )
        ).first()
        if anchor is not None:
            query = query.where(tuple_(DmarcAggregateReport.date_range_begin, DmarcAggregateRecord.id) < anchor)

    query = query.order_by(DmarcAggregateReport.date_range_begin.desc(), DmarcAggregateRecord.id.desc()).limit(limit)
    return (await db.execute(query)).all()


async def get_record_detail(
    db: AsyncSession, domain_id: UUID, record_id: UUID
) -> tuple[DmarcAggregateRecord, DmarcAggregateReport] | None:
    return (
        await db.execute(
            select(DmarcAggregateRecord, DmarcAggregateReport)
            .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
            .where(DmarcAggregateRecord.id == record_id, DmarcAggregateRecord.domain_id == domain_id)
        )
    ).first()


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


async def report_totals(
    db: AsyncSession,
    domain_id: UUID,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
):
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    query = (
        select(
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
            func.coalesce(
                func.sum(case((DmarcAggregateRecord.disposition == Disposition.none, DmarcAggregateRecord.count), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case((DmarcAggregateRecord.disposition == Disposition.quarantine, DmarcAggregateRecord.count), else_=0)
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((DmarcAggregateRecord.disposition == Disposition.reject, DmarcAggregateRecord.count), else_=0)),
                0,
            ),
            func.count(func.distinct(DmarcAggregateRecord.report_id)),
            func.max(DmarcAggregateReport.received_at),
        )
        .select_from(DmarcAggregateRecord)
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )
    return (await db.execute(query)).one()


async def top_failing_source_row(
    db: AsyncSession,
    domain_id: UUID,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
):
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    failed_sum = func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)
    query = (
        select(DmarcAggregateRecord.source_ip, failed_sum.label("failed"))
        .select_from(DmarcAggregateRecord)
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.source_ip)
        .having(failed_sum > 0)
        .order_by(failed_sum.desc())
        .limit(1)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )
    return (await db.execute(query)).first()


_GROUPED_BY_COLUMNS = {
    "source": DmarcAggregateRecord.source_ip,
    "reporter": DmarcAggregateReport.org_name,
    "disposition": DmarcAggregateRecord.disposition,
}


async def report_records_grouped(
    db: AsyncSession,
    domain_id: UUID,
    by: str,
    *,
    since: datetime | None,
    disposition: Disposition | None,
    spf_result: AuthResult | None,
    dkim_result: AuthResult | None,
    reporter: str | None,
    source_ip: str | None,
) -> Sequence:
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    group_col = _GROUPED_BY_COLUMNS[by]

    def _sum_where(condition):
        return func.coalesce(func.sum(case((condition, DmarcAggregateRecord.count), else_=0)), 0)

    query = (
        select(
            group_col,
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            func.count(func.distinct(DmarcAggregateRecord.report_id)),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.none),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.quarantine),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.reject),
            _sum_where(dmarc_pass),
        )
        .select_from(DmarcAggregateRecord)
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(group_col)
    )
    query = _apply_report_filters(
        query, since=since, disposition=disposition, spf_result=spf_result, dkim_result=dkim_result,
        reporter=reporter, source_ip=source_ip,
    )
    return (await db.execute(query)).all()


async def list_unmatched_aggregate_reports(
    db: AsyncSession, organization_id: UUID, limit: int
) -> Sequence[DmarcAggregateReport]:
    """Aggregate reports whose policy_published domain didn't match any
    registered Domain in this org — surfaced rather than silently dropped
    (see domain_matcher.py)."""
    result = await db.execute(
        select(DmarcAggregateReport)
        .where(DmarcAggregateReport.organization_id == organization_id, DmarcAggregateReport.domain_id.is_(None))
        .order_by(DmarcAggregateReport.received_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def registered_domains_by_name(db: AsyncSession, organization_id: UUID) -> dict[str, UUID]:
    result = await db.execute(select(Domain.id, Domain.name).where(Domain.organization_id == organization_id))
    return {name: domain_id for domain_id, name in result.all()}


async def dismissed_domain_names(db: AsyncSession, organization_id: UUID) -> set[str]:
    result = await db.execute(
        select(DismissedDetectedDomain.name).where(DismissedDetectedDomain.organization_id == organization_id)
    )
    return set(result.scalars().all())


async def unmatched_aggregate_domain_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    result = await db.execute(
        select(
            DmarcAggregateReport.policy_published_domain,
            func.count(func.distinct(DmarcAggregateReport.id)),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
        )
        .outerjoin(DmarcAggregateRecord, DmarcAggregateRecord.report_id == DmarcAggregateReport.id)
        .where(DmarcAggregateReport.organization_id == organization_id, DmarcAggregateReport.domain_id.is_(None))
        .group_by(DmarcAggregateReport.policy_published_domain)
    )
    return result.all()


async def unmatched_record_header_from_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    """A report can match a registered domain (policy_published/domain — e.g.
    the organizational domain, whose policy a subdomain's mail is
    evaluated under) while individual records within it don't — RFC 7489
    §7.2 keeps header_from separate per record for exactly this reason.
    Not filtering by domain_id here (unlike the sibling functions above):
    match_domain's ancestor walk means a record's header_from almost always
    resolves to *some* domain_id once its parent is registered, even though
    header_from itself was never registered — so domain_id IS NULL would
    systematically miss this case. The caller filters out exact registered
    names instead, which is what actually catches it.

    Also excludes records whose source_ip is already reviewed and marked
    "blocked" for the domain_id they resolved to — same idiom
    domain_rating.py's _windowed_totals uses for the rating itself, just
    correlated per-row instead of pinned to one domain_id, since each
    header_from here can resolve to a different ancestor. A sender the org
    has already dealt with (confirmed spoofing/abuse) shouldn't keep
    prompting "add this domain" forever."""
    blocked_source_ips_for_row = (
        select(SourceIpIdentity.source_ip)
        .join(SenderReview, SenderReview.service_label == SourceIpIdentity.service_label)
        .where(
            SenderReview.domain_id == DmarcAggregateRecord.domain_id,
            SenderReview.status == SenderReviewStatus.blocked,
        )
        .correlate(DmarcAggregateRecord)
    )
    result = await db.execute(
        select(
            DmarcAggregateRecord.header_from,
            func.count(func.distinct(DmarcAggregateRecord.report_id)),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
        )
        .where(
            DmarcAggregateRecord.organization_id == organization_id,
            DmarcAggregateRecord.source_ip.not_in(blocked_source_ips_for_row),
        )
        .group_by(DmarcAggregateRecord.header_from)
    )
    return result.all()


async def unmatched_tls_rpt_domain_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    result = await db.execute(
        select(
            TlsRptReport.policy_domain,
            func.count(func.distinct(TlsRptReport.id)),
            func.coalesce(func.sum(TlsRptReport.summary_success_count + TlsRptReport.summary_failure_count), 0),
        )
        .where(TlsRptReport.organization_id == organization_id, TlsRptReport.domain_id.is_(None))
        .group_by(TlsRptReport.policy_domain)
    )
    return result.all()


async def unmatched_forensic_domain_counts(db: AsyncSession, organization_id: UUID) -> Sequence:
    result = await db.execute(
        select(DmarcForensicReport.reported_domain, func.count())
        .where(
            DmarcForensicReport.organization_id == organization_id,
            DmarcForensicReport.domain_id.is_(None),
            DmarcForensicReport.reported_domain.is_not(None),
            DmarcForensicReport.reported_domain != "",
        )
        .group_by(DmarcForensicReport.reported_domain)
    )
    return result.all()


async def per_source_ip_volume_breakdown(
    db: AsyncSession, domain_id: UUID, *, since: datetime | None = None
) -> Sequence:
    """Volume/alignment/disposition aggregated per-source-IP for one domain
    — the SQL half of dmarc_analytics.py's service_breakdown (identifying
    each IP's sending service and rolling up by service label happens in
    Python there, not here). `since` windows to reports whose traffic
    period begins on or after it (joining the parent report's
    date_range_begin), so a decommissioned host or a retired sender with
    no recent traffic simply drops out. None = all-time."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    def _sum_where(condition):
        return func.sum(case((condition, DmarcAggregateRecord.count), else_=0))

    query = (
        select(
            DmarcAggregateRecord.source_ip,
            func.sum(DmarcAggregateRecord.count),
            _sum_where(DmarcAggregateRecord.spf_result == AuthResult.pass_),
            _sum_where(DmarcAggregateRecord.dkim_result == AuthResult.pass_),
            _sum_where(dmarc_pass),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.none),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.quarantine),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.reject),
        )
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.source_ip)
    )
    if since is not None:
        query = query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateReport.date_range_begin >= since)
    return (await db.execute(query)).all()


async def dismiss_detected_domain_name(db: AsyncSession, *, organization_id: UUID, name: str, dismissed_by: UUID) -> None:
    """ON CONFLICT DO NOTHING rather than add()-then-catch: dismissing an
    already-dismissed name (e.g. a retried click) is a no-op, not an
    error — same race-tolerant idiom as sender_inventory's SenderReview
    upsert."""
    stmt = pg_insert(DismissedDetectedDomain).values(
        organization_id=organization_id, name=name, dismissed_by=dismissed_by
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["organization_id", "name"])
    await db.execute(stmt)
