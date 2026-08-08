"""DB-aware wrappers around score.compute_rating: fetch the observed DMARC
pass rate + latest DNS-check findings for one domain, score them, and (via
domain_policy_readiness) judge whether a domain has room to tighten its
DMARC policy and is actually ready to. Shared by /domains/{id}/rating,
/domains/ranked, /dmarc/posture, and the action-queue's
domain_ready_for_stricter_policy / enforcement_readiness_notice rules —
one implementation of each computation, not four."""

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import AuthResult, CheckType, DomainVerificationStatus, SenderReviewStatus
from app.models.sender_review import SenderReview
from app.models.source_ip_identity import SourceIpIdentity
from app.services.rating.score import DomainRating, compute_rating

READY_TO_ENFORCE_MIN_VOLUME = 50
READY_TO_ENFORCE_MIN_PASS_PCT = 98.0
# How far back the rating (and high_volume_failure, which is meant to stay
# consistent with it) looks — a rolling window rather than all-time, so a
# domain isn't scored against traffic from a year ago forever.
RATING_WINDOW_DAYS = 90
_NEXT_POLICY_RUNG = {"none": "quarantine", "quarantine": "reject"}


async def _windowed_totals(db: AsyncSession, domain_id: uuid.UUID) -> tuple[int, int]:
    """(total_count, dmarc_pass_count) over the last RATING_WINDOW_DAYS days,
    excluding traffic from senders explicitly marked blocked (SenderReview)
    — confirmed spoofing/abuse a domain owner has already dealt with
    shouldn't keep dragging the score down forever. Pending/unreviewed
    traffic still counts normally (only an explicit "blocked" excludes).

    Windowed on DmarcAggregateReport.date_range_begin (the report's mail
    period), not DmarcAggregateRecord.created_at (ingestion time) — same
    join/filter idiom dmarc_trend/_apply_report_filters/policy_stability_days
    already use.

    The blocked-sender exclusion is a pure SQL join — source_ip (INET) on
    both DmarcAggregateRecord and the global SourceIpIdentity cache, then
    SourceIpIdentity.service_label against SenderReview.service_label — no
    identify_many() round-trip and no commit needed, keeping this read-only
    like the rest of this module. A source_ip with no cached identity yet
    simply can't match a blocked service_label, so it's conservatively
    still counted (correct default: only proven-blocked traffic drops out)."""
    since = datetime.now(timezone.utc) - timedelta(days=RATING_WINDOW_DAYS)
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )
    blocked_source_ips = (
        select(SourceIpIdentity.source_ip)
        .join(SenderReview, SenderReview.service_label == SourceIpIdentity.service_label)
        .where(SenderReview.domain_id == domain_id, SenderReview.status == SenderReviewStatus.blocked)
    )
    total_count, pass_count = (
        await db.execute(
            select(
                func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
                func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
            )
            .select_from(DmarcAggregateRecord)
            .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
            .where(
                DmarcAggregateRecord.domain_id == domain_id,
                DmarcAggregateReport.date_range_begin >= since,
                DmarcAggregateRecord.source_ip.not_in(blocked_source_ips),
            )
        )
    ).one()
    return int(total_count), int(pass_count)


async def latest_findings_by_type(db: AsyncSession, domain_id: uuid.UUID) -> dict[CheckType, list[DnsCheckResult]]:
    """Every DnsCheckResult row from the domain's most recent recheck,
    grouped by check_type — the same "latest checked_at" fetch used
    verbatim in app/routers/dns_checks.py's list_latest_checks."""
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


async def compute_domain_rating(
    db: AsyncSession, domain: Domain, findings_by_type: dict[CheckType, list] | None = None
) -> tuple[DomainRating, int]:
    """Returns (rating, total_message_count) — callers that need message
    volume for their own weighting/thresholds would otherwise have to
    re-run the same sum query themselves. `findings_by_type` can be passed
    in by a caller that already fetched it (e.g. for check_status_counts on
    /domains/ranked) to avoid querying it twice."""
    total_count, pass_count = await _windowed_totals(db, domain.id)

    if findings_by_type is None:
        findings_by_type = await latest_findings_by_type(db, domain.id)

    rating = compute_rating(
        findings_by_type=findings_by_type,
        dmarc_pass_count=pass_count,
        total_message_count=total_count,
        mail_profile=domain.mail_profile,
    )
    return rating, total_count


@dataclasses.dataclass
class PolicyReadiness:
    eligible: bool  # has a published policy that isn't already p=reject — room to tighten exists
    ready: bool  # eligible AND pass-rate/volume criteria met
    latest_policy: str | None
    next_rung: str | None
    pass_rate_pct: float | None
    total_volume: int


async def domain_policy_readiness(
    db: AsyncSession,
    domain: Domain,
    rating: DomainRating | None = None,
    total_volume: int | None = None,
) -> PolicyReadiness:
    """`rating`/`total_volume` can be passed in by a caller that already
    called compute_domain_rating for this domain, to avoid running that
    query twice."""
    if domain.verification_status != DomainVerificationStatus.verified:
        return PolicyReadiness(False, False, None, None, None, 0)

    latest_policy = (
        await db.execute(
            select(DmarcAggregateReport.policy_p)
            .where(DmarcAggregateReport.domain_id == domain.id)
            .order_by(DmarcAggregateReport.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    next_rung = _NEXT_POLICY_RUNG.get((latest_policy or "").lower())
    if next_rung is None:
        return PolicyReadiness(False, False, latest_policy, None, None, 0)

    if rating is None or total_volume is None:
        rating, total_volume = await compute_domain_rating(db, domain)

    if rating.insufficient_data or total_volume < READY_TO_ENFORCE_MIN_VOLUME:
        return PolicyReadiness(True, False, latest_policy, next_rung, None, total_volume)

    pass_rate_factor = next((f for f in rating.factors if f.factor == "dmarc_pass_rate"), None)
    pass_rate_pct = pass_rate_factor.score_pct if pass_rate_factor is not None else None
    ready = pass_rate_pct is not None and pass_rate_pct >= READY_TO_ENFORCE_MIN_PASS_PCT
    return PolicyReadiness(True, ready, latest_policy, next_rung, pass_rate_pct, total_volume)


async def policy_stability_days(db: AsyncSession, domain_id: uuid.UUID, max_days: int = 60) -> int:
    """How many consecutive days (counting back from the most recent
    reporting day, within the last `max_days`) every report has agreed on
    the same published policy — a lightweight "has enforcement been
    stable" signal derived entirely from policy_p snapshots already stored
    per report, no new tracking column needed. A day where reports
    disagree on policy_p (e.g. a same-day DNS change) breaks the streak,
    same as a day at a genuinely different policy would."""
    since = datetime.now(timezone.utc) - timedelta(days=max_days)
    day_col = func.date_trunc("day", DmarcAggregateReport.date_range_begin)
    rows = (
        await db.execute(
            select(day_col, DmarcAggregateReport.policy_p)
            .where(DmarcAggregateReport.domain_id == domain_id, DmarcAggregateReport.date_range_begin >= since)
            .distinct()
            .order_by(day_col.desc())
        )
    ).all()
    if not rows:
        return 0

    policies_by_day: dict = {}
    for day, policy in rows:
        policies_by_day.setdefault(day, set()).add(policy)

    current_policy = rows[0][1]
    streak = 0
    for day in sorted(policies_by_day.keys(), reverse=True):
        if policies_by_day[day] != {current_policy}:
            break
        streak += 1
    return streak
