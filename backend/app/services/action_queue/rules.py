"""Stateless action-queue rules: each function inspects current data and
returns zero or more ActionItems. No dismiss/acknowledge table anywhere in
this app (see the Overview plan addendum) — an item simply stops appearing
once whatever it flagged resolves itself, computed fresh on every request.

Each item is deliberately terse: a title with the metric baked in, and a
one-line action hint — this is a scannable issue list, not a paragraph
feed. Items are ranked by CATEGORY first (how urgent/high-signal the kind
of problem is), then by severity within a category — see CATEGORY_* below
and the sort in app/routers/action_queue.py.

Threshold constants below are first-pass values, same spirit as the rating
weights in app/services/rating/score.py: meant to be eyeballed against real
domains and retuned, not treated as settled."""

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import (
    CheckType,
    ConsentStatus,
    DomainMailProfile,
    DomainVerificationStatus,
    SenderReviewStatus,
    SyncStatus,
)
from app.models.mailbox_connection import MailboxConnection
from app.models.sender_review import SenderReview
from app.services.dns_checks.dmarc_record import check_rua_destination, fetch_current_dmarc_record
from app.services.dns_checks.resolver import DnsLookupError
from app.services.rating.domain_rating import _windowed_totals, compute_domain_rating, domain_policy_readiness

UNKNOWN_SENDER_VOLUME_THRESHOLD = 50
STALE_MAILBOX_DAYS = 10
LOW_COMPLIANCE_SERIOUS_BELOW = 70.0
LOW_COMPLIANCE_CRITICAL_BELOW = 50.0
HIGH_VOLUME_FAILURE_MIN_COUNT = 50
HIGH_VOLUME_FAILURE_MIN_PCT = 10.0
HIGH_VOLUME_FAILURE_CRITICAL_PCT = 30.0
ALIGNMENT_ISSUE_MIN_PCT = 50.0

# Priority buckets, most urgent/high-signal first — mail actively failing
# at volume outranks a bare compliance number, which outranks ingestion
# plumbing, and so on down to "you're doing fine, here's the next step."
# A likely-spoofed sender outranks even a bare "unreviewed sender" (that's
# just unlooked-at, could be good or bad) since it's already a confirmed,
# high-confidence-bad pattern the policy is fending off — but stays below
# high-volume-failure, which is about mail actively getting through.
CATEGORY_HIGH_VOLUME_FAILURE = 1
CATEGORY_LIKELY_SPOOFED = 2
CATEGORY_LOW_COMPLIANCE = 3
CATEGORY_INGESTION = 4
CATEGORY_UNKNOWN_SENDER = 5
CATEGORY_DNS_BLOCKING = 6
CATEGORY_POLICY_READY = 7


@dataclasses.dataclass
class ActionItem:
    severity: str  # "good" | "warning" | "serious" | "critical" | "neutral" — same roles as the badge system
    category: int  # CATEGORY_* — primary sort key, see module docstring
    title: str  # one line, metric baked in, e.g. "cloud-stuff.pro: 60% compliance"
    action_hint: str  # one short imperative line, e.g. "Open DNS/authentication checks"
    domain_id: str | None


async def reviewed_service_labels(db: AsyncSession, domain_id: uuid.UUID) -> set[str]:
    """service_labels with an explicit approved/ignored/blocked review row
    for this domain — a service is "unreviewed" if it's missing here
    entirely (no sender_reviews row at all) or the row is still pending,
    both read the same way by callers so this doesn't depend on the
    lazy-create-on-read timing of the sender-inventory endpoint having
    already run for this domain."""
    reviewed_result = await db.execute(
        select(SenderReview.service_label).where(
            SenderReview.domain_id == domain_id,
            SenderReview.status.in_(
                [SenderReviewStatus.approved, SenderReviewStatus.ignored, SenderReviewStatus.blocked]
            ),
        )
    )
    return {row[0] for row in reviewed_result.all()}


def unreviewed_high_volume_senders(services: list[dict], reviewed_labels: set[str]) -> list[dict]:
    """Services at/above UNKNOWN_SENDER_VOLUME_THRESHOLD volume with no
    reviewed_labels entry — shared by unknown_sender_above_threshold below
    and the policy builder's stricter-policy blocking check. Pure function,
    no I/O of its own."""
    return [
        s
        for s in services
        if s["service_label"] not in reviewed_labels and s["volume"] >= UNKNOWN_SENDER_VOLUME_THRESHOLD
    ]


async def unknown_sender_above_threshold(db: AsyncSession, domain: Domain, services: list[dict]) -> list[ActionItem]:
    """Takes an already-computed `services` breakdown (the caller computes
    it once per domain and shares it with sender_alignment_issue too)
    rather than calling service_breakdown itself — avoids running the same
    PTR-identification aggregation twice per domain per action-queue
    request."""
    if not services:
        return []

    reviewed_labels = await reviewed_service_labels(db, domain.id)

    items = []
    for s in unreviewed_high_volume_senders(services, reviewed_labels):
        items.append(
            ActionItem(
                severity="warning",
                category=CATEGORY_UNKNOWN_SENDER,
                title=f'{domain.name}: unreviewed sender "{s["service_label"]}" ({s["volume"]:,} msgs)',
                action_hint="Review in Sender Inventory",
                domain_id=str(domain.id),
            )
        )
    return items


async def likely_spoofed_sender(db: AsyncSession, domain: Domain, services: list[dict]) -> list[ActionItem]:
    """Flags services service_breakdown already marked likely_spoofed (see
    dmarc_analytics.py — mostly rejected/quarantined and essentially
    unauthenticated on both mechanisms) that haven't been reviewed yet.
    Deliberately no volume floor unlike unreviewed_high_volume_senders —
    a handful of spoofed messages is still worth a look, unlike a handful
    of messages from an otherwise-plausible unreviewed sender."""
    if not services:
        return []

    reviewed_labels = await reviewed_service_labels(db, domain.id)

    items = []
    for s in services:
        if s["service_label"] in reviewed_labels or not s.get("likely_spoofed"):
            continue
        items.append(
            ActionItem(
                severity="serious",
                category=CATEGORY_LIKELY_SPOOFED,
                title=f'{domain.name}: likely spoofed sender "{s["service_label"]}" ({s["volume"]:,} msgs)',
                action_hint="Review in Sender Inventory — approve if legitimate, or block to confirm",
                domain_id=str(domain.id),
            )
        )
    return items


async def mailbox_stopped_receiving_reports(db: AsyncSession, organization_id: uuid.UUID) -> list[ActionItem]:
    """Org-wide (one mailbox per org), so this is independent of any
    domain_id filter the caller applies elsewhere — a stale mailbox affects
    every domain's data quality at once, which is exactly the "quietly lies"
    concern that made mailbox health a first-class Overview concept."""
    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == organization_id))
    ).scalar_one_or_none()
    if connection is None or connection.consent_status != ConsentStatus.granted:
        return []
    if connection.last_sync_status == SyncStatus.error:
        return []  # a failing sync is already surfaced by the mailbox-health widget — don't duplicate
    if connection.last_sync_at is None:
        return []  # never synced yet at all — not "stopped", just not started

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_MAILBOX_DAYS)
    if connection.last_sync_at < cutoff:
        return []  # sync itself hasn't run recently either — a scheduling/worker problem, not this rule

    latest_report_at = (
        await db.execute(
            select(func.max(DmarcAggregateReport.received_at)).where(
                DmarcAggregateReport.organization_id == organization_id
            )
        )
    ).scalar_one_or_none()
    if latest_report_at is not None and latest_report_at >= cutoff:
        return []

    return [
        ActionItem(
            severity="serious",
            category=CATEGORY_INGESTION,
            title=f"Mailbox: no reports in {STALE_MAILBOX_DAYS}d despite healthy sync",
            action_hint="Check senders' rua= or mailbox delivery",
            domain_id=None,
        )
    ]


async def domain_ready_for_stricter_policy(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    r = await domain_policy_readiness(db, domain)
    if not r.ready:
        return []
    return [
        ActionItem(
            severity="good",
            category=CATEGORY_POLICY_READY,
            title=f"{domain.name}: ready for p={r.next_rung}",
            action_hint=f"{r.pass_rate_pct}% pass rate over {r.total_volume:,} msgs — open Policy Builder",
            domain_id=str(domain.id),
        )
    ]


async def enforcement_readiness_notice(db: AsyncSession, domains: list[Domain]) -> list[ActionItem]:
    """Org-wide only — the caller should pass this the FULL org domain list
    regardless of any single-domain filter it's otherwise applying. Only
    fires when there's real headroom to tighten policy somewhere
    (eligible_count > 0 and none of those are ready): if every domain is
    already at p=reject, eligible_count is 0 and this stays correctly
    silent rather than flagging "0 ready to enforce" forever, which would
    otherwise be permanently true even in the best possible state."""
    eligible_count = 0
    ready_count = 0
    for domain in domains:
        r = await domain_policy_readiness(db, domain)
        if r.eligible:
            eligible_count += 1
            if r.ready:
                ready_count += 1

    if eligible_count == 0 or ready_count > 0:
        return []

    return [
        ActionItem(
            severity="neutral",
            category=CATEGORY_POLICY_READY,
            title=f"{eligible_count} domain{'s' if eligible_count != 1 else ''} could tighten policy",
            action_hint="See Domains Needing Attention for what's blocking them",
            domain_id=None,
        )
    ]


async def low_compliance_domain(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """Only fires below LOW_COMPLIANCE_SERIOUS_BELOW (70%) — a B-grade
    domain in the 80s isn't queue-worthy on compliance alone, it just needs
    to not have a *specific* blocking issue, which the other rules already
    surface individually."""
    if domain.verification_status != DomainVerificationStatus.verified:
        return []
    rating, _total = await compute_domain_rating(db, domain)
    if rating.insufficient_data or rating.score is None:
        return []
    if rating.score >= LOW_COMPLIANCE_SERIOUS_BELOW:
        return []
    severity = "critical" if rating.score < LOW_COMPLIANCE_CRITICAL_BELOW else "serious"
    return [
        ActionItem(
            severity=severity,
            category=CATEGORY_LOW_COMPLIANCE,
            title=f"{domain.name}: {rating.score}% compliance (grade {rating.grade})",
            action_hint="Open DNS/authentication checks",
            domain_id=str(domain.id),
        )
    ]


async def high_volume_failure(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """Shares compute_domain_rating's own windowed pass-rate computation
    (90-day rolling window, blocked-sender traffic excluded) via
    _windowed_totals, so this item's failing-message count never disagrees
    with what the rating itself is scoring."""
    total, passed = await _windowed_totals(db, domain.id)
    failed = total - passed
    if total == 0 or failed < HIGH_VOLUME_FAILURE_MIN_COUNT:
        return []

    failed_pct = failed / total * 100
    if failed_pct < HIGH_VOLUME_FAILURE_MIN_PCT:
        return []

    severity = "critical" if failed_pct >= HIGH_VOLUME_FAILURE_CRITICAL_PCT else "serious"
    return [
        ActionItem(
            severity=severity,
            category=CATEGORY_HIGH_VOLUME_FAILURE,
            title=f"{domain.name}: {failed:,} failing messages ({round(failed_pct, 1)}%)",
            action_hint="Review the Outbound email table for the worst sender",
            domain_id=str(domain.id),
        )
    ]


def sender_alignment_issue(domain: Domain, services: list[dict]) -> list[ActionItem]:
    """Distinct from unknown_sender_above_threshold: fires on alignment
    quality regardless of review status — an *approved* sender can still be
    misconfigured. Pure function over an already-computed services
    breakdown, no I/O of its own."""
    items = []
    for s in services:
        if s["volume"] < UNKNOWN_SENDER_VOLUME_THRESHOLD:
            continue
        spf_pct, dkim_pct = s["spf_aligned_pct"], s["dkim_aligned_pct"]
        if spf_pct is None or dkim_pct is None:
            continue
        if spf_pct >= ALIGNMENT_ISSUE_MIN_PCT or dkim_pct >= ALIGNMENT_ISSUE_MIN_PCT:
            continue
        items.append(
            ActionItem(
                severity="warning",
                category=CATEGORY_HIGH_VOLUME_FAILURE,
                title=f'{domain.name} sender "{s["service_label"]}": {spf_pct}% SPF / {dkim_pct}% DKIM',
                action_hint="Fix SPF/DKIM alignment",
                domain_id=str(domain.id),
            )
        )
    return items


async def spf_lookup_limit_risk(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """Directly surfaces the existing SPF near/over-limit finding
    (app/services/dns_checks/spf.py) from the last check run — no new
    checker logic, just reads what's already stored."""
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at))
        .where(DnsCheckResult.domain_id == domain.id, DnsCheckResult.check_type == CheckType.spf)
        .scalar_subquery()
    )
    rows = (
        (
            await db.execute(
                select(DnsCheckResult).where(
                    DnsCheckResult.domain_id == domain.id,
                    DnsCheckResult.check_type == CheckType.spf,
                    DnsCheckResult.checked_at == latest_ts,
                )
            )
        )
        .scalars()
        .all()
    )

    items = []
    for r in rows:
        details = r.details or {}
        lookup_count = details.get("lookup_count")
        limit = details.get("limit")
        if lookup_count is None or limit is None or lookup_count < limit - 2:
            continue
        items.append(
            ActionItem(
                severity="critical" if lookup_count > limit else "warning",
                category=CATEGORY_DNS_BLOCKING,
                title=f"{domain.name}: SPF near lookup limit ({lookup_count}/{limit})",
                action_hint="Simplify SPF includes",
                domain_id=str(domain.id),
            )
        )
    return items


async def rua_destination_broken(db: AsyncSession, domain: Domain, mailbox_address: str | None) -> list[ActionItem]:
    """Catches the trap this whole onboarding/reporting area is meant to
    prevent: a domain can be verified, pass every DNS check, and still
    never send this product a single report if rua= isn't (or no longer
    is) pointed at the connected mailbox. Deliberately silent on "no
    DMARC record at all" (dmarc.py's checker + low_compliance_domain
    already cover that loudly) and on a transient lookup_error — this rule
    is specifically about a record that exists but doesn't report here."""
    if mailbox_address is None or domain.verification_status != DomainVerificationStatus.verified:
        return []
    result = await check_rua_destination(domain.name, mailbox_address)
    if result.status in ("correct", "not_configured", "lookup_error"):
        return []
    title = (
        f"{domain.name}: no rua= address configured"
        if result.status == "no_rua"
        else f"{domain.name}: rua= not reaching this mailbox"
    )
    return [
        ActionItem(
            severity="serious",
            category=CATEGORY_INGESTION,
            title=title,
            action_hint="Open Policy Builder to fix rua=",
            domain_id=str(domain.id),
        )
    ]


async def parked_domain_not_locked_down(domain: Domain) -> list[ActionItem]:
    """receive_only/parked domains have no legitimate outbound mail, so
    there's nothing to wait for — unlike every other policy-readiness rule
    here, this checks the LIVE published record rather than report history,
    since a domain like this will never accumulate any."""
    if domain.mail_profile == DomainMailProfile.sends_mail:
        return []
    if domain.verification_status != DomainVerificationStatus.verified:
        return []
    try:
        record = await fetch_current_dmarc_record(domain.name)
    except DnsLookupError:
        return []
    current_p = (record.tags.get("p") if record else None) or ""
    if current_p.lower() == "reject":
        return []
    label = "receive-only" if domain.mail_profile == DomainMailProfile.receive_only else "not used for mail"
    return [
        ActionItem(
            severity="warning",
            category=CATEGORY_POLICY_READY,
            title=f"{domain.name}: marked {label} but not locked to p=reject",
            action_hint="Open Policy Builder to lock down now — no legitimate senders to protect",
            domain_id=str(domain.id),
        )
    ]
