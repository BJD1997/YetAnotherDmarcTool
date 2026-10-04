"""The Overview's posture tiles: compliance, failed volume, current policy
(one domain) or the policy spread (all domains), report freshness, new
senders and how many domains are ready for a stricter policy — one bundle
rather than six tiny endpoints."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.repositories import dmarc_reports as dmarc_reports_repo
from app.services.rating.domain_rating import compute_domain_rating, domain_policy_readiness


async def _policies_and_readiness(
    db: AsyncSession, domains: list[Domain], single_domain: bool
) -> tuple[str | None, dict[str, int], int]:
    """(the one domain's policy, policy → number of domains, domains ready
    for a stricter policy)."""
    single_policy: str | None = None
    distribution: dict[str, int] = {}
    ready = 0
    for domain in domains:
        rating, total = None, 0
        if domain.verification_status == DomainVerificationStatus.verified:
            rating, total, _failed = await compute_domain_rating(db, domain)
        readiness = await domain_policy_readiness(db, domain, rating=rating, total_volume=total)
        if single_domain:
            single_policy = readiness.latest_policy
        elif readiness.latest_policy is not None:
            distribution[readiness.latest_policy] = distribution.get(readiness.latest_policy, 0) + 1
        if readiness.ready:
            ready += 1
    return single_policy, distribution, ready


def _hours_since(moment: datetime | None) -> float | None:
    if moment is None:
        return None
    return round((datetime.now(timezone.utc) - moment).total_seconds() / 3600, 1)


async def compute_posture(
    db: AsyncSession, organization_id: uuid.UUID, domains: list[Domain], domain_id: uuid.UUID | None, days: int
) -> dict:
    """`domains` is the one domain asked for (`domain_id` set) or all of the
    organization's; the caller checks ownership and sets the RLS context."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    single_policy, distribution, ready = await _policies_and_readiness(db, domains, domain_id is not None)

    # The DMARC pass rate of the messages in the selected range — not the
    # domains' grade, which also weighs DNS checks over a fixed window and
    # so never moved with the range.
    total_volume, failed_volume = await dmarc_reports_repo.message_volume_for_org_since(
        db, organization_id, since, domain_id=domain_id
    )
    if domain_id is not None:
        last_received_at = await dmarc_reports_repo.last_report_received_at_for_domain(db, domain_id)
    else:
        last_received_at = await dmarc_reports_repo.last_report_received_at_for_org(db, organization_id)
    new_senders = await dmarc_reports_repo.count_new_pending_senders_since(db, organization_id, since, domain_id=domain_id)

    return {
        "compliance_pct": round(100 * (total_volume - failed_volume) / total_volume, 1) if total_volume else None,
        "current_policy": single_policy,
        "policy_distribution": distribution if domain_id is None else None,
        "failed_volume": failed_volume,
        "report_freshness_hours": _hours_since(last_received_at),
        "new_sender_count": int(new_senders),
        "ready_to_enforce_count": ready,
    }
