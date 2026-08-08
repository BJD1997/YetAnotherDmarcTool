import itertools
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dismissed_detected_domain import DismissedDetectedDomain
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainMailProfile, DomainVerificationStatus, SenderReviewStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.sender_review import SenderReview
from app.models.source_ip_identity import SourceIpIdentity
from app.models.tls_rpt import TlsRptReport
from app.models.user import User
from app.services.action_queue.rules import reviewed_service_labels, unreviewed_high_volume_senders
from app.services.dmarc_analytics import service_breakdown
from app.services.dmarc_narrative import dkim_narratives, spf_narratives
from app.services.dns_checks.dmarc_record import DmarcRecordInfo, check_rua_destination, fetch_current_dmarc_record
from app.services.dns_checks.resolver import DnsLookupError
from app.services.rating.domain_rating import (
    READY_TO_ENFORCE_MIN_PASS_PCT,
    PolicyReadiness,
    compute_domain_rating,
    domain_policy_readiness,
    policy_stability_days,
)
from app.services.rating.score import DomainRating
from app.services.source_identification.service_identifier import identify_many

router = APIRouter(tags=["dmarc-reports"])


async def _get_owned_domain(db: AsyncSession, domain_id: uuid.UUID, organization_id: uuid.UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return domain


@router.get("/domains/{domain_id}/dmarc/summary")
async def dmarc_summary(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    await _get_owned_domain(db, domain_id, user.organization_id)

    # A message passes DMARC if EITHER SPF or DKIM is aligned-pass (RFC 7489)
    # — policy_evaluated.{dkim,spf} in the aggregate report already reflect
    # the receiver's own alignment-aware judgement, so no separate
    # "alignment" bookkeeping is needed beyond what's already stored.
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    totals = await db.execute(
        select(
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
        ).where(DmarcAggregateRecord.domain_id == domain_id)
    )
    total_count, pass_count = totals.one()

    disposition_rows = await db.execute(
        select(DmarcAggregateRecord.disposition, func.sum(DmarcAggregateRecord.count))
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.disposition)
    )
    by_disposition = {disposition.value: count for disposition, count in disposition_rows.all()}

    report_count = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.domain_id == domain_id)
    )

    current_policy = (
        await db.execute(
            select(DmarcAggregateReport.policy_p)
            .where(DmarcAggregateReport.domain_id == domain_id)
            .order_by(DmarcAggregateReport.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return {
        "total_message_count": int(total_count),
        "dmarc_pass_count": int(pass_count),
        "dmarc_fail_count": int(total_count) - int(pass_count),
        "by_disposition": by_disposition,
        "report_count": report_count.scalar_one(),
        "current_policy": current_policy,
    }


@router.get("/domains/{domain_id}/rating")
async def domain_rating(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    domain = await _get_owned_domain(db, domain_id, user.organization_id)

    if domain.verification_status != DomainVerificationStatus.verified:
        return {"not_verified": True, "insufficient_data": True, "score": None, "grade": None, "factors": []}

    rating, _total = await compute_domain_rating(db, domain)
    return {
        "not_verified": False,
        "insufficient_data": rating.insufficient_data,
        "score": rating.score,
        "grade": rating.grade,
        "factors": [
            {"factor": f.factor, "weight": f.weight, "score_pct": f.score_pct, "detail": f.detail} for f in rating.factors
        ],
    }


@router.get("/domains/{domain_id}/dmarc/sources")
async def dmarc_sources(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Per-sending-service breakdown (not per raw source_ip — header_from is
    constant per domain so grouping by it, as this endpoint used to, added
    nothing; source_ip alone is the real grouping key, further rolled up by
    identified sending service)."""
    await _get_owned_domain(db, domain_id, user.organization_id)
    services = await service_breakdown(db, domain_id)
    await db.commit()  # persists any newly-resolved source_ip_identities cache rows
    return services


class SenderReviewUpdateRequest(BaseModel):
    status: SenderReviewStatus | None = None
    owner: str | None = None
    notes: str | None = None


def _sender_review_out(review: SenderReview) -> dict:
    return {
        "status": review.status.value,
        "owner": review.owner,
        "notes": review.notes,
        "created_at": review.created_at.isoformat(),
        "reviewed_at": review.reviewed_at.isoformat() if review.reviewed_at else None,
    }


@router.get("/domains/{domain_id}/dmarc/sender-inventory")
async def sender_inventory(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Richer sibling of /dmarc/sources: the same per-service breakdown,
    joined against sender_reviews for approval status/owner/notes. Any
    service seen here with no existing review row gets one lazily
    upserted with status=pending — this doubles as "new sender" detection
    via the row's created_at, no separate first-seen tracking needed."""
    await _get_owned_domain(db, domain_id, user.organization_id)
    services = await service_breakdown(db, domain_id)
    if not services:
        return []

    review_rows = (
        (await db.execute(select(SenderReview).where(SenderReview.domain_id == domain_id))).scalars().all()
    )
    reviews_by_label = {r.service_label: r for r in review_rows}

    missing_labels = [s["service_label"] for s in services if s["service_label"] not in reviews_by_label]
    if missing_labels:
        stmt = pg_insert(SenderReview).values(
            [
                {
                    "id": uuid.uuid4(),
                    "organization_id": user.organization_id,
                    "domain_id": domain_id,
                    "service_label": label,
                    "status": SenderReviewStatus.pending.value,
                }
                for label in missing_labels
            ]
        )
        # ON CONFLICT DO NOTHING rather than get-then-insert — two orgs'
        # (or two tabs') page-loads racing on the same (domain_id,
        # service_label) shouldn't 500 on the unique constraint, same
        # race-tolerant spirit as identify_many's cache upsert.
        stmt = stmt.on_conflict_do_nothing(index_elements=["domain_id", "service_label"])
        await db.execute(stmt)
        await db.flush()
        review_rows = (
            (await db.execute(select(SenderReview).where(SenderReview.domain_id == domain_id))).scalars().all()
        )
        reviews_by_label = {r.service_label: r for r in review_rows}
    await db.commit()

    return [{**s, **_sender_review_out(reviews_by_label[s["service_label"]])} for s in services]


@router.patch("/domains/{domain_id}/dmarc/sender-inventory/{service_label}")
async def update_sender_review(
    domain_id: uuid.UUID,
    service_label: str,
    body: SenderReviewUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    await _get_owned_domain(db, domain_id, user.organization_id)

    result = await db.execute(
        select(SenderReview).where(SenderReview.domain_id == domain_id, SenderReview.service_label == service_label)
    )
    review = result.scalar_one_or_none()
    if review is None:
        # The sender-inventory GET hasn't lazily created this row yet (e.g.
        # a client patching straight from action-queue data) — create it
        # here too rather than 404ing on a service that legitimately exists.
        review = SenderReview(organization_id=user.organization_id, domain_id=domain_id, service_label=service_label)
        db.add(review)

    if body.status is not None:
        review.status = body.status
        review.reviewed_by = user.id
        review.reviewed_at = datetime.now(timezone.utc)
    if body.owner is not None:
        review.owner = body.owner
    if body.notes is not None:
        review.notes = body.notes

    await db.flush()
    await db.refresh(review)
    await db.commit()
    return {"service_label": review.service_label, **_sender_review_out(review)}


@router.get("/dmarc/trend")
async def dmarc_trend(
    domain_id: uuid.UUID | None = Query(None),
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    if domain_id is not None:
        await _get_owned_domain(db, domain_id, user.organization_id)

    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    def _sum_where(condition):
        return func.coalesce(func.sum(case((condition, DmarcAggregateRecord.count), else_=0)), 0)

    since = datetime.now(timezone.utc) - timedelta(days=days)
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
            DmarcAggregateRecord.organization_id == user.organization_id,
            DmarcAggregateReport.date_range_begin >= since,
        )
        .group_by(day)
        .order_by(day)
    )
    if domain_id is not None:
        query = query.where(DmarcAggregateRecord.domain_id == domain_id)

    rows = (await db.execute(query)).all()
    return [
        {
            "date": d.date().isoformat(),
            "total": int(total),
            "dmarc_pass": int(pass_count),
            "spf_aligned": int(spf_pass),
            "dkim_aligned": int(dkim_pass),
            "rejected": int(rejected),
        }
        for d, total, pass_count, spf_pass, dkim_pass, rejected in rows
    ]


@router.get("/dmarc/posture")
async def dmarc_posture(
    domain_id: uuid.UUID | None = Query(None),
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Bundled compliance/policy/failed-volume/freshness/new-senders/
    ready-to-enforce, rather than six tiny endpoints — matches the
    "don't over-fragment" instinct already applied in dmarc_summary."""
    if domain_id is not None:
        domains = [await _get_owned_domain(db, domain_id, user.organization_id)]
    else:
        result = await db.execute(select(Domain).where(Domain.organization_id == user.organization_id))
        domains = result.scalars().all()

    since = datetime.now(timezone.utc) - timedelta(days=days)

    scored: list[tuple[float, int]] = []
    policy_counts: dict[str, int] = {}
    single_domain_policy: str | None = None
    ready_to_enforce_count = 0

    for domain in domains:
        rating: DomainRating | None = None
        total: int = 0
        if domain.verification_status == DomainVerificationStatus.verified:
            rating, total = await compute_domain_rating(db, domain)
            if not rating.insufficient_data and rating.score is not None:
                scored.append((rating.score, total))

        readiness = await domain_policy_readiness(db, domain, rating=rating, total_volume=total)
        if domain_id is not None:
            single_domain_policy = readiness.latest_policy
        elif readiness.latest_policy is not None:
            policy_counts[readiness.latest_policy] = policy_counts.get(readiness.latest_policy, 0) + 1
        if readiness.ready:
            ready_to_enforce_count += 1

    compliance_pct: float | None = None
    if scored:
        # Weight by volume, but a domain with zero volume in-window still
        # gets a small nonzero weight (1) rather than being dropped from the
        # average entirely.
        weighted_sum = sum(score * max(total, 1) for score, total in scored)
        weight_total = sum(max(total, 1) for _, total in scored)
        compliance_pct = round(weighted_sum / weight_total, 1)

    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    failed_volume_query = select(
        func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)
    ).where(DmarcAggregateRecord.organization_id == user.organization_id)
    freshness_query = select(func.max(DmarcAggregateReport.received_at)).where(
        DmarcAggregateReport.organization_id == user.organization_id
    )
    new_sender_query = select(func.count()).select_from(SenderReview).where(
        SenderReview.organization_id == user.organization_id,
        SenderReview.status == SenderReviewStatus.pending,
        SenderReview.created_at >= since,
    )
    if domain_id is not None:
        failed_volume_query = failed_volume_query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateRecord.domain_id == domain_id, DmarcAggregateReport.date_range_begin >= since)
        freshness_query = freshness_query.where(DmarcAggregateReport.domain_id == domain_id)
        new_sender_query = new_sender_query.where(SenderReview.domain_id == domain_id)
    else:
        failed_volume_query = failed_volume_query.join(
            DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id
        ).where(DmarcAggregateReport.date_range_begin >= since)

    failed_volume = (await db.execute(failed_volume_query)).scalar_one()
    last_received_at = (await db.execute(freshness_query)).scalar_one_or_none()
    new_sender_count = (await db.execute(new_sender_query)).scalar_one()

    report_freshness_hours = (
        round((datetime.now(timezone.utc) - last_received_at).total_seconds() / 3600, 1)
        if last_received_at is not None
        else None
    )

    return {
        "compliance_pct": compliance_pct,
        "current_policy": single_domain_policy,
        "policy_distribution": policy_counts if domain_id is None else None,
        "failed_volume": int(failed_volume),
        "report_freshness_hours": report_freshness_hours,
        "new_sender_count": int(new_sender_count),
        "ready_to_enforce_count": ready_to_enforce_count,
    }


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


@router.get("/domains/{domain_id}/dmarc/reports/by-day")
async def dmarc_reports_by_day(
    domain_id: uuid.UUID,
    limit: int = Query(300, ge=1, le=1000),
    before_id: uuid.UUID | None = Query(None),
    days: int | None = Query(None, ge=1, le=365),
    disposition: Disposition | None = Query(None),
    spf_result: AuthResult | None = Query(None),
    dkim_result: AuthResult | None = Query(None),
    reporter: str | None = Query(None),
    source_ip: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Row granularity is one DmarcAggregateRecord (one sending host within
    one report), not one whole report — a report with several source IPs
    shows as several rows on its day. Keyset-paginated on
    (date_range_begin, record id) rather than offset, so pages stay stable
    as new reports keep arriving between requests. Filters apply to the
    keyset query itself, not after the fact, since a busy domain can have
    thousands of records."""
    await _get_owned_domain(db, domain_id, user.organization_id)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

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
        query,
        since=since,
        disposition=disposition,
        spf_result=spf_result,
        dkim_result=dkim_result,
        reporter=reporter,
        source_ip=source_ip,
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
    rows = (await db.execute(query)).all()

    # Resolve every distinct source_ip on this page to its identified
    # sending service (same cache-first lookup service_breakdown uses), so
    # each row can show "SMTP2GO" next to the raw address instead of the
    # bare IP alone.
    identities = await identify_many(db, [str(r.source_ip) for r in rows])
    await db.commit()

    days_out = []
    for date, group_iter in itertools.groupby(rows, key=lambda r: r.date_range_begin.date()):
        group = list(group_iter)
        report_ids = {r.report_pk for r in group}
        days_out.append(
            {
                "date": date.isoformat(),
                "report_count": len(report_ids),
                "message_count": sum(r.count for r in group),
                "accepted": sum(r.count for r in group if r.disposition == Disposition.none),
                "quarantined": sum(r.count for r in group if r.disposition == Disposition.quarantine),
                "rejected": sum(r.count for r in group if r.disposition == Disposition.reject),
                "rows": [
                    {
                        "record_id": str(r.id),
                        "org_name": r.org_name,
                        "source_ip": str(r.source_ip),
                        "service_label": identities[str(r.source_ip)].service_label,
                        "count": r.count,
                        "disposition": r.disposition.value,
                        "spf_result": r.spf_result.value,
                        "dkim_result": r.dkim_result.value,
                    }
                    for r in group
                ],
            }
        )

    return {"days": days_out, "has_more": len(rows) == limit}


@router.get("/domains/{domain_id}/dmarc/reports/summary")
async def dmarc_reports_summary(
    domain_id: uuid.UUID,
    days: int | None = Query(None, ge=1, le=365),
    disposition: Disposition | None = Query(None),
    spf_result: AuthResult | None = Query(None),
    dkim_result: AuthResult | None = Query(None),
    reporter: str | None = Query(None),
    source_ip: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Summary bar for the Reports page — same filter vocabulary as
    by-day/grouped, so switching a filter updates the totals and the rows
    together."""
    await _get_owned_domain(db, domain_id, user.organization_id)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    totals_query = (
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
    totals_query = _apply_report_filters(
        totals_query,
        since=since,
        disposition=disposition,
        spf_result=spf_result,
        dkim_result=dkim_result,
        reporter=reporter,
        source_ip=source_ip,
    )
    total, passed, accepted, quarantined, rejected, report_count, last_received_at = (
        await db.execute(totals_query)
    ).one()

    failed_sum = func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)
    top_failing_query = (
        select(DmarcAggregateRecord.source_ip, failed_sum.label("failed"))
        .select_from(DmarcAggregateRecord)
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.source_ip)
        .having(failed_sum > 0)
        .order_by(failed_sum.desc())
        .limit(1)
    )
    top_failing_query = _apply_report_filters(
        top_failing_query,
        since=since,
        disposition=disposition,
        spf_result=spf_result,
        dkim_result=dkim_result,
        reporter=reporter,
        source_ip=source_ip,
    )
    top_failing_row = (await db.execute(top_failing_query)).first()

    top_failing_source = None
    if top_failing_row is not None:
        ip_str = str(top_failing_row.source_ip)
        identity = (await identify_many(db, [ip_str]))[ip_str]
        top_failing_source = {
            "source_ip": ip_str,
            "service_label": identity.service_label,
            "failed_count": int(top_failing_row.failed),
        }
    await db.commit()

    return {
        "total_reports": int(report_count),
        "total_messages": int(total),
        "accepted": int(accepted),
        "quarantined": int(quarantined),
        "rejected": int(rejected),
        "dmarc_pass_pct": round(passed / total * 100, 1) if total else None,
        "top_failing_source": top_failing_source,
        "last_report_received_at": last_received_at.isoformat() if last_received_at else None,
    }


_GROUPED_BY_COLUMNS = {
    "source": DmarcAggregateRecord.source_ip,
    "reporter": DmarcAggregateReport.org_name,
    "disposition": DmarcAggregateRecord.disposition,
}


@router.get("/domains/{domain_id}/dmarc/reports/grouped")
async def dmarc_reports_grouped(
    domain_id: uuid.UUID,
    by: str = Query(..., pattern="^(source|reporter|disposition)$"),
    days: int | None = Query(None, ge=1, le=365),
    disposition: Disposition | None = Query(None),
    spf_result: AuthResult | None = Query(None),
    dkim_result: AuthResult | None = Query(None),
    reporter: str | None = Query(None),
    source_ip: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """The Reports page's Source/Reporter/Disposition grouping views — small
    cardinality per domain, so one GROUP BY query with no pagination."""
    await _get_owned_domain(db, domain_id, user.organization_id)
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None

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
        query,
        since=since,
        disposition=disposition,
        spf_result=spf_result,
        dkim_result=dkim_result,
        reporter=reporter,
        source_ip=source_ip,
    )
    rows = (await db.execute(query)).all()

    identities = {}
    if by == "source":
        identities = await identify_many(db, [str(row[0]) for row in rows])
        await db.commit()

    results = []
    for key_val, volume, report_count, accepted, quarantined, rejected, passed in rows:
        if by == "source":
            key_str = str(key_val)
            label = identities[key_str].service_label
        elif by == "disposition":
            key_str = key_val.value
            label = key_val.value
        else:
            key_str = key_val
            label = key_val
        results.append(
            {
                "key": key_str,
                "label": label,
                "message_count": int(volume),
                "report_count": int(report_count),
                "accepted": int(accepted),
                "quarantined": int(quarantined),
                "rejected": int(rejected),
                "dmarc_pass_pct": round(int(passed) / int(volume) * 100, 1) if volume else None,
            }
        )
    results.sort(key=lambda r: -r["message_count"])
    return results


@router.get("/domains/{domain_id}/dmarc/records/{record_id}")
async def dmarc_record_detail(
    domain_id: uuid.UUID,
    record_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    await _get_owned_domain(db, domain_id, user.organization_id)

    row = (
        await db.execute(
            select(DmarcAggregateRecord, DmarcAggregateReport)
            .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
            .where(DmarcAggregateRecord.id == record_id, DmarcAggregateRecord.domain_id == domain_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
    record, report = row

    return {
        "id": str(record.id),
        "report": {
            "id": str(report.id),
            "report_id": report.report_id,
            "org_name": report.org_name,
            "email": report.email,
            "date_range_begin": report.date_range_begin.isoformat(),
            "date_range_end": report.date_range_end.isoformat(),
            "policy_p": report.policy_p,
            "policy_sp": report.policy_sp,
            "policy_pct": report.policy_pct,
            "policy_adkim": report.policy_adkim,
            "policy_aspf": report.policy_aspf,
        },
        "source_ip": str(record.source_ip),
        "count": record.count,
        "disposition": record.disposition.value,
        "spf_result": record.spf_result.value,
        "dkim_result": record.dkim_result.value,
        "header_from": record.header_from,
        "envelope_from": record.envelope_from,
        "envelope_to": record.envelope_to,
        "auth_results": record.auth_results,
        "spf_narrative": spf_narratives(record.auth_results, str(record.source_ip), record.header_from),
        "dkim_narrative": dkim_narratives(record.auth_results, record.header_from),
        "verdict": {
            "spf_aligned": record.spf_result == AuthResult.pass_,
            "dkim_aligned": record.dkim_result == AuthResult.pass_,
            "dmarc_aligned": record.spf_result == AuthResult.pass_ or record.dkim_result == AuthResult.pass_,
            "disposition_applied": record.disposition.value,
        },
    }


@router.get("/dmarc/unmatched")
async def unmatched_reports(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Aggregate reports whose policy_published domain didn't match any
    registered Domain in this org — surfaced rather than silently dropped
    (see domain_matcher.py)."""
    result = await db.execute(
        select(DmarcAggregateReport)
        .where(DmarcAggregateReport.organization_id == user.organization_id, DmarcAggregateReport.domain_id.is_(None))
        .order_by(DmarcAggregateReport.received_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(r.id),
            "org_name": r.org_name,
            "report_id": r.report_id,
            "policy_published_domain": r.policy_published_domain,
            "received_at": r.received_at.isoformat(),
        }
        for r in result.scalars().all()
    ]


@router.get("/dmarc/detected-domains")
async def detected_domains(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Distinct domain names seen in reports that didn't match anything
    registered — the same unmatched bucket as /dmarc/unmatched, but grouped
    by domain name (across aggregate, TLS-RPT, and forensic reports) so it
    reads as "domains you could add" rather than a raw report list. Each
    entry is annotated with whether it looks like a subdomain of a domain
    you've already registered (in which case "Add" can create it correctly
    parented in one step) or of another *detected* domain (informational —
    our one-level nesting model means the detected apex should generally be
    added first)."""
    detected: dict[str, dict] = {}

    # Needed up front (not just for the suggested-parent annotation below):
    # match_domain's ancestor walk means a subdomain of an already-registered
    # domain always resolves to that ancestor's domain_id, never NULL — so a
    # domain_id IS NULL filter alone would never catch it. Filtering out
    # exact registered names instead (further down) is what actually surfaces
    # that case, e.g. a subdomain sending real mail whose parent is
    # registered but which itself never was, exactly the gap that left
    # web02.davidshosting.nl invisible until its mail started bouncing.
    registered_result = await db.execute(
        select(Domain.id, Domain.name).where(Domain.organization_id == user.organization_id)
    )
    registered = {name: domain_id for domain_id, name in registered_result.all()}
    registered_names = set(registered.keys())

    dismissed_names = set(
        (
            await db.execute(
                select(DismissedDetectedDomain.name).where(
                    DismissedDetectedDomain.organization_id == user.organization_id
                )
            )
        )
        .scalars()
        .all()
    )

    agg_result = await db.execute(
        select(
            DmarcAggregateReport.policy_published_domain,
            func.count(func.distinct(DmarcAggregateReport.id)),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
        )
        .outerjoin(DmarcAggregateRecord, DmarcAggregateRecord.report_id == DmarcAggregateReport.id)
        .where(
            DmarcAggregateReport.organization_id == user.organization_id,
            DmarcAggregateReport.domain_id.is_(None),
        )
        .group_by(DmarcAggregateReport.policy_published_domain)
    )
    for name, report_count, message_volume in agg_result.all():
        detected[name] = {"report_count": report_count, "message_volume": int(message_volume)}

    # A report can match a registered domain (policy_published/domain — e.g.
    # the organizational domain, whose policy a subdomain's mail is
    # evaluated under) while individual records within it don't — RFC 7489
    # §7.2 keeps header_from separate per record for exactly this reason.
    # Not filtering by domain_id here (unlike the other three queries in this
    # function): match_domain's ancestor walk means a record's header_from
    # almost always resolves to *some* domain_id once its parent is
    # registered, even though header_from itself was never registered — so
    # domain_id IS NULL would systematically miss this case. Filtering out
    # exact registered names below is what actually catches it.
    #
    # Also excludes records whose source_ip is already reviewed and marked
    # "blocked" for the domain_id they resolved to — same idiom
    # domain_rating.py's _windowed_totals uses for the rating itself, just
    # correlated per-row instead of pinned to one domain_id, since each
    # header_from here can resolve to a different ancestor. A sender the org
    # has already dealt with (confirmed spoofing/abuse) shouldn't keep
    # prompting "add this domain" forever.
    blocked_source_ips_for_row = (
        select(SourceIpIdentity.source_ip)
        .join(SenderReview, SenderReview.service_label == SourceIpIdentity.service_label)
        .where(
            SenderReview.domain_id == DmarcAggregateRecord.domain_id,
            SenderReview.status == SenderReviewStatus.blocked,
        )
        .correlate(DmarcAggregateRecord)
    )
    unmatched_records_result = await db.execute(
        select(
            DmarcAggregateRecord.header_from,
            func.count(func.distinct(DmarcAggregateRecord.report_id)),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
        )
        .where(
            DmarcAggregateRecord.organization_id == user.organization_id,
            DmarcAggregateRecord.source_ip.not_in(blocked_source_ips_for_row),
        )
        .group_by(DmarcAggregateRecord.header_from)
    )
    for name, report_count, message_volume in unmatched_records_result.all():
        if name in registered_names:
            continue
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count
        entry["message_volume"] += int(message_volume)

    tls_result = await db.execute(
        select(
            TlsRptReport.policy_domain,
            func.count(func.distinct(TlsRptReport.id)),
            func.coalesce(
                func.sum(TlsRptReport.summary_success_count + TlsRptReport.summary_failure_count), 0
            ),
        )
        .where(TlsRptReport.organization_id == user.organization_id, TlsRptReport.domain_id.is_(None))
        .group_by(TlsRptReport.policy_domain)
    )
    for name, report_count, message_volume in tls_result.all():
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count
        entry["message_volume"] += int(message_volume)

    forensic_result = await db.execute(
        select(DmarcForensicReport.reported_domain, func.count())
        .where(
            DmarcForensicReport.organization_id == user.organization_id,
            DmarcForensicReport.domain_id.is_(None),
            DmarcForensicReport.reported_domain.is_not(None),
            DmarcForensicReport.reported_domain != "",
        )
        .group_by(DmarcForensicReport.reported_domain)
    )
    for name, report_count in forensic_result.all():
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count

    for name in dismissed_names:
        detected.pop(name, None)

    detected_names = set(detected.keys())

    items = []
    for name, stats in detected.items():
        suggested_parent_id: str | None = None
        suggested_parent_name: str | None = None
        relationship = "apex"

        # Prefer the longest (most specific) registered ancestor, in case
        # more than one registered domain is a suffix match.
        for reg_name, reg_id in sorted(registered.items(), key=lambda kv: -len(kv[0])):
            if name.endswith(f".{reg_name}"):
                suggested_parent_id = str(reg_id)
                suggested_parent_name = reg_name
                relationship = "subdomain_of_registered"
                break

        if suggested_parent_id is None:
            for other in sorted(detected_names, key=len):
                if other != name and name.endswith(f".{other}"):
                    relationship = "subdomain_of_detected"
                    suggested_parent_name = other
                    break

        items.append(
            {
                "name": name,
                "report_count": stats["report_count"],
                "message_volume": stats["message_volume"],
                "relationship": relationship,
                "suggested_parent_id": suggested_parent_id,
                "suggested_parent_name": suggested_parent_name,
            }
        )

    # Apex-looking entries first, then shorter (more likely-apex) names first.
    items.sort(key=lambda x: (x["relationship"] != "apex", len(x["name"]), -x["report_count"]))
    return items


@router.post("/dmarc/detected-domains/{name}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_detected_domain(
    name: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> None:
    """Marks a detected-but-not-registered name as "not mine" so it stops
    surfacing — for lookalikes, unrelated senders, or anything else the org
    has looked at and decided isn't worth registering as a domain."""
    name = name.strip().lower().rstrip(".")
    # ON CONFLICT DO NOTHING rather than add()-then-catch: dismissing an
    # already-dismissed name (e.g. a retried click) is a no-op, not an
    # error — same race-tolerant idiom as sender_inventory's SenderReview
    # upsert above.
    stmt = pg_insert(DismissedDetectedDomain).values(
        organization_id=user.organization_id, name=name, dismissed_by=user.id
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["organization_id", "name"])
    await db.execute(stmt)
    await db.commit()


POLICY_STABILITY_MIN_DAYS = 14


def _build_policy_recommendation(
    *,
    domain: Domain,
    rating: DomainRating,
    total: int,
    readiness: PolicyReadiness,
    stability_days: int,
    blockers: list[dict],
) -> dict:
    """DMARCbis-compliant (RFC 9989 Appendix A.6 removes pct= entirely, so
    this never generates or recommends one) — mirrors the two worked
    examples from the product spec almost verbatim: a blocked domain gets
    told exactly which sender/volume is holding it back, a ready domain
    gets told exactly why it's ready. Staged rollout is handled by gating
    the policy MOVE itself on pass-rate + stability + no unreviewed
    senders, rather than by publishing a partial-enforcement percentage —
    the safety net current-generation pct= provided is now this app's own
    report analysis instead.

    Also recommends np= (RFC 9989's new non-existent-subdomain policy,
    distinct from sp= which covers subdomains that exist but lack their
    own DMARC record) — see _recommend_np below for why this is decided
    independently of whether the main policy move itself is blocked."""
    recommendation = _build_base_recommendation(
        domain=domain, rating=rating, total=total, readiness=readiness, stability_days=stability_days, blockers=blockers
    )
    recommendation["np"] = _recommend_np(recommendation["policy"])
    return recommendation


def _recommend_np(recommended_policy: str) -> str | None:
    """Non-existent subdomains can never have a legitimate sender by
    definition, so hardening them (np=reject) is safe regardless of how
    cautious the main policy rollout is being — recommended as soon as
    quarantine or reject is in play anywhere in the recommendation, not
    gated behind the same pass-rate/blocker checks the main policy is."""
    return "reject" if recommended_policy in ("quarantine", "reject") else None


def _build_base_recommendation(
    *,
    domain: Domain,
    rating: DomainRating,
    total: int,
    readiness: PolicyReadiness,
    stability_days: int,
    blockers: list[dict],
) -> dict:
    current_policy = readiness.latest_policy

    # A domain with no legitimate outbound mail has nothing to wait for —
    # rating.insufficient_data would otherwise always be true here (no
    # senders means no report volume, permanently) and recommend staying at
    # monitor-only forever. Checked first, ahead of that branch.
    if domain.mail_profile != DomainMailProfile.sends_mail:
        label = "receive-only" if domain.mail_profile == DomainMailProfile.receive_only else "not used for mail"
        return {
            "policy": "reject",
            "reasoning": (
                f"This domain is marked {label} — there's no legitimate outbound mail to protect, so lock down to "
                "p=reject now rather than waiting on report data that won't arrive."
            ),
            "blocked": False,
            "blocking_reason": None,
        }

    if rating.insufficient_data:
        return {
            "policy": "none",
            "reasoning": "No reports yet — start at monitor-only until data arrives.",
            "blocked": False,
            "blocking_reason": None,
        }

    if blockers:
        blockers_sorted = sorted(blockers, key=lambda s: -s["volume"])
        top = blockers_sorted[0]
        extra = f" and {len(blockers_sorted) - 1} other unreviewed sender(s)" if len(blockers_sorted) > 1 else ""
        blocking_reason = (
            f'{top["volume"]} messages from an unreviewed sender ("{top["service_label"]}"){extra} — '
            "review or approve them in Sender Inventory before tightening enforcement."
        )
        return {
            "policy": current_policy or "none",
            "reasoning": f"Stay at {f'p={current_policy}' if current_policy else 'monitor-only'} for now.",
            "blocked": True,
            "blocking_reason": blocking_reason,
        }

    pass_rate_factor = next((f for f in rating.factors if f.factor == "dmarc_pass_rate"), None)
    pass_rate_pct = pass_rate_factor.score_pct if pass_rate_factor is not None else None

    if pass_rate_pct is None or pass_rate_pct < READY_TO_ENFORCE_MIN_PASS_PCT:
        failed = round(total * (1 - (pass_rate_pct or 0) / 100))
        return {
            "policy": current_policy or "none",
            "reasoning": (
                f"Stay at {f'p={current_policy}' if current_policy else 'monitor-only'} for now. {failed} of "
                f"{total} messages are failing DMARC ({round(100 - (pass_rate_pct or 0), 1)}% fail rate) — fix "
                "or approve the senders responsible before enforcing."
            ),
            "blocked": True,
            "blocking_reason": f"DMARC pass rate is {pass_rate_pct or 0}%, below the {READY_TO_ENFORCE_MIN_PASS_PCT}% bar.",
        }

    if current_policy is None or current_policy == "none":
        return {
            "policy": "quarantine",
            "reasoning": (
                f"This domain looks ready for p=quarantine. DMARC pass rate is {pass_rate_pct}%, no unreviewed "
                "high-volume senders were seen, and reports are arriving normally."
            ),
            "blocked": False,
            "blocking_reason": None,
        }

    if current_policy == "quarantine":
        if stability_days >= POLICY_STABILITY_MIN_DAYS:
            return {
                "policy": "reject",
                "reasoning": (
                    f"Stable at p=quarantine for {stability_days} days with a {pass_rate_pct}% pass rate — "
                    "ready to move to p=reject."
                ),
                "blocked": False,
                "blocking_reason": None,
            }
        return {
            "policy": "quarantine",
            "reasoning": (
                f"Pass rate looks good ({pass_rate_pct}%), but p=quarantine has only been stable for "
                f"{stability_days} day(s) so far — give it a bit longer before moving to p=reject."
            ),
            "blocked": False,
            "blocking_reason": None,
        }

    return {
        "policy": "reject",
        "reasoning": "Already at the strongest policy (p=reject) — nothing further to recommend.",
        "blocked": False,
        "blocking_reason": None,
    }


@router.get("/domains/{domain_id}/dmarc/policy-builder")
async def dmarc_policy_builder(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """Bundles everything the policy-builder UI needs in one call: the live
    current record, whether rua= actually reaches the connected mailbox,
    and a recommended next record — built entirely from data this app
    already computes elsewhere (compute_domain_rating, domain_policy_readiness,
    service_breakdown+sender_reviews), not new analysis."""
    domain = await _get_owned_domain(db, domain_id, user.organization_id)

    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == user.organization_id))
    ).scalar_one_or_none()
    # A domain with its own hosted address (see POST
    # /domains/{id}/hosted-report-address) is using that as its dedicated
    # report destination — prefer it over the org's shared MailboxConnection,
    # which local-auth orgs never have at all.
    mailbox_address = domain.hosted_report_address or (connection.mailbox_address if connection is not None else None)

    lookup_error = False
    current: DmarcRecordInfo | None
    try:
        current = await fetch_current_dmarc_record(domain.name)
    except DnsLookupError:
        current = None
        lookup_error = True

    if mailbox_address is not None:
        rua_result = await check_rua_destination(domain.name, mailbox_address)
        rua_destination = {"status": rua_result.status, "current_targets": rua_result.current_targets}
    else:
        rua_destination = {"status": "no_mailbox", "current_targets": []}

    rating, total = await compute_domain_rating(db, domain)
    readiness = await domain_policy_readiness(db, domain, rating=rating, total_volume=total)
    stability_days = await policy_stability_days(db, domain.id)

    services = await service_breakdown(db, domain.id)
    reviewed_labels = await reviewed_service_labels(db, domain.id)
    blockers = unreviewed_high_volume_senders(services, reviewed_labels)
    await db.commit()  # persists identify_many's cache upsert from service_breakdown, see its own docstring

    recommendation = _build_policy_recommendation(
        domain=domain, rating=rating, total=total, readiness=readiness, stability_days=stability_days, blockers=blockers
    )

    return {
        "current_record": {"raw": current.raw, "tags": current.tags} if current is not None else None,
        "current_record_lookup_error": lookup_error,
        "rua_destination": rua_destination,
        "org_mailbox_address": mailbox_address,
        "hosted_report_address": domain.hosted_report_address,
        "policy_stability_days": stability_days,
        "recommendation": recommendation,
    }
