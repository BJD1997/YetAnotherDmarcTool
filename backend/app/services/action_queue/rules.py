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
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.enums import (
    CheckType,
    ConsentStatus,
    DomainMailProfile,
    DomainVerificationStatus,
    SyncStatus,
)
from app.repositories.dmarc_reports import last_report_received_at_for_org, list_reviewed_service_labels_for_domain
from app.repositories.dns_checks import latest_dns_check_results_of_type_for_domain
from app.repositories.mailbox_connections import get_org_mailbox_connection
from app.repositories.trends import get_trend
from app.services.dns_checks.dmarc_record import check_rua_destination, fetch_current_dmarc_record
from app.services.dns_checks.resolver import DnsLookupError
from app.services.rating.domain_rating import (
    READY_TO_ENFORCE_MIN_PASS_PCT,
    READY_TO_ENFORCE_MIN_VOLUME,
    _windowed_totals,
    rating_window_days,
    compute_domain_rating,
    domain_policy_readiness,
    latest_findings_by_type,
)
from app.services.rating.score import worst_status

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
    title: str  # one line, metric baked in, e.g. "example.com: 60% compliance"
    action_hint: str  # one short imperative line, e.g. "Open DNS/authentication checks"
    domain_id: str | None
    # Where clicking this item should actually go — the specific check, sender,
    # or builder it's about, not just the domain's general overview page. A
    # frontend route, e.g. "/domains/{id}/senders?highlight={label}" or
    # "/domains/{id}/dns?open=policy-builder". None only for the rare item with
    # no more specific destination than the domain page itself already gives.
    link_path: str | None = None
    # The concrete underlying finding driving this item, e.g. a DNS check's
    # own summary text — "Open DNS/authentication checks" alone doesn't say
    # WHICH check or why. None when the title/action_hint already say
    # everything there is (most rules), or when the item isn't traceable to
    # one specific finding.
    evidence: str | None = None


def _sender_link(domain_id: uuid.UUID, service_label: str) -> str:
    return f"/domains/{domain_id}/senders?highlight={quote(service_label, safe='')}"


# Reverse of score.py's WEIGHTS/_DIRECT_CHECK_FACTORS keys, so a rating
# factor that dragged the score down can be traced back to the specific
# DnsCheckResult rows it was computed from.
_FACTOR_TO_CHECK_TYPE: dict[str, CheckType] = {
    "spf": CheckType.spf,
    "dkim": CheckType.dkim,
    "mx": CheckType.mx,
    "starttls": CheckType.starttls,
    "mta_sts": CheckType.mta_sts,
    "dane": CheckType.dane,
    "tls_rpt": CheckType.tls_rpt,
    "dmarc_policy": CheckType.dmarc,
}


def _worst_finding_summary(findings_by_type: dict[CheckType, list], factor: str) -> str | None:
    """The specific finding text behind the lowest-scoring rating factor —
    e.g. dmarc_policy's actual DnsCheckResult.summary ("...doesn't authorize
    it — most receivers will refuse to send reports there"), not just the
    factor's own bare status keyword ("fail")."""
    check_type = _FACTOR_TO_CHECK_TYPE.get(factor)
    if check_type is None:
        return None
    findings = findings_by_type.get(check_type) or []
    if not findings:
        return None
    overall_worst = worst_status([f.status for f in findings])
    worst_finding = next((f for f in findings if f.status == overall_worst), None)
    return worst_finding.summary if worst_finding is not None else None


async def reviewed_service_labels(db: AsyncSession, domain_id: uuid.UUID) -> set[str]:
    """service_labels with an explicit approved/ignored/blocked review row
    for this domain — a service is "unreviewed" if it's missing here
    entirely (no sender_reviews row at all) or the row is still pending,
    both read the same way by callers so this doesn't depend on the
    lazy-create-on-read timing of the sender-inventory endpoint having
    already run for this domain."""
    return await list_reviewed_service_labels_for_domain(db, domain_id)


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


def unknown_sender_above_threshold(domain: Domain, services: list[dict], reviewed_labels: set[str]) -> list[ActionItem]:
    """Takes an already-computed `services` breakdown (the caller computes
    it once per domain and shares it with sender_alignment_issue too)
    rather than calling service_breakdown itself — avoids running the same
    PTR-identification aggregation twice per domain per action-queue
    request. Same reasoning for `reviewed_labels`: the caller fetches it
    once per domain and shares it with likely_spoofed_sender, rather than
    this and likely_spoofed_sender each independently re-querying it."""
    if not services:
        return []

    items = []
    for s in unreviewed_high_volume_senders(services, reviewed_labels):
        if s.get("likely_spoofed"):
            continue  # likely_spoofed_sender already asks for the same review
        items.append(
            ActionItem(
                severity="warning",
                category=CATEGORY_UNKNOWN_SENDER,
                title=f'{domain.name}: unreviewed sender "{s["service_label"]}" ({s["volume"]:,} msgs)',
                action_hint="Not reviewed yet: approve or block it",
                domain_id=str(domain.id),
                link_path=_sender_link(domain.id, s["service_label"]),
            )
        )
    return items


def likely_spoofed_sender(domain: Domain, services: list[dict], reviewed_labels: set[str]) -> list[ActionItem]:
    """Flags services service_breakdown already marked likely_spoofed (see
    dmarc_analytics.py — mostly rejected/quarantined and essentially
    unauthenticated on both mechanisms) that haven't been reviewed yet.
    Deliberately no volume floor unlike unreviewed_high_volume_senders —
    a handful of spoofed messages is still worth a look, unlike a handful
    of messages from an otherwise-plausible unreviewed sender. Takes
    `reviewed_labels` precomputed for the same reason services is —
    see unknown_sender_above_threshold's docstring."""
    if not services:
        return []

    items = []
    for s in services:
        if s["service_label"] in reviewed_labels or not s.get("likely_spoofed"):
            continue
        items.append(
            ActionItem(
                severity="serious",
                category=CATEGORY_LIKELY_SPOOFED,
                title=f'{domain.name}: likely spoofed sender "{s["service_label"]}" ({s["volume"]:,} msgs)',
                action_hint="Likely spoofed: approve if legitimate, or block to confirm",
                domain_id=str(domain.id),
                link_path=_sender_link(domain.id, s["service_label"]),
            )
        )
    return items


async def mailbox_stopped_receiving_reports(db: AsyncSession, organization_id: uuid.UUID) -> list[ActionItem]:
    """Org-wide (one mailbox per org), so this is independent of any
    domain_id filter the caller applies elsewhere — a stale mailbox affects
    every domain's data quality at once, which is exactly the "quietly lies"
    concern that made mailbox health a first-class Overview concept."""
    connection = await get_org_mailbox_connection(db, organization_id)
    if connection is None or connection.consent_status != ConsentStatus.granted:
        return []
    if connection.last_sync_status == SyncStatus.error:
        return []  # a failing sync is already surfaced by the mailbox-health widget — don't duplicate
    if connection.last_sync_at is None:
        return []  # never synced yet at all — not "stopped", just not started

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_MAILBOX_DAYS)
    if connection.last_sync_at < cutoff:
        return []  # sync itself hasn't run recently either — a scheduling/worker problem, not this rule

    latest_report_at = await last_report_received_at_for_org(db, organization_id)
    if latest_report_at is not None and latest_report_at >= cutoff:
        return []

    return [
        ActionItem(
            severity="serious",
            category=CATEGORY_INGESTION,
            title=f"Mailbox: no reports in {STALE_MAILBOX_DAYS}d despite healthy sync",
            action_hint="Check senders' rua= or mailbox delivery",
            domain_id=None,
            link_path="/settings",
        )
    ]


async def domain_ready_for_stricter_policy(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """Ready for the next policy step — or, when there is a next step but
    the domain isn't ready, what's in the way. Both open the domain's
    Policy Builder."""
    r = await domain_policy_readiness(db, domain)
    if not r.eligible:
        return []
    link_path = f"/domains/{domain.id}/dns?open=policy-builder"
    if r.ready:
        return [
            ActionItem(
                severity="good",
                category=CATEGORY_POLICY_READY,
                title=f"{domain.name}: ready for p={r.next_rung}",
                action_hint=f"{r.pass_rate_pct}% pass rate over {r.total_volume:,} msgs — open Policy Builder",
                domain_id=str(domain.id),
                link_path=link_path,
            )
        ]
    if r.pass_rate_pct is None:
        days = await rating_window_days(db, domain.organization_id)
        blocker = f"{r.total_volume:,} of {READY_TO_ENFORCE_MIN_VOLUME} messages needed in the last {days} days"
    else:
        blocker = f"{r.pass_rate_pct}% pass rate, needs {READY_TO_ENFORCE_MIN_PASS_PCT:g}%"
    return [
        ActionItem(
            severity="neutral",
            category=CATEGORY_POLICY_READY,
            title=f"{domain.name}: not ready for p={r.next_rung} yet",
            action_hint=f"{blocker} — open Policy Builder",
            domain_id=str(domain.id),
            link_path=link_path,
        )
    ]


async def low_compliance_domain(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """Only fires below LOW_COMPLIANCE_SERIOUS_BELOW (70%) — a B-grade
    domain in the 80s isn't queue-worthy on compliance alone, it just needs
    to not have a *specific* blocking issue, which the other rules already
    surface individually."""
    if domain.verification_status != DomainVerificationStatus.verified:
        return []
    findings_by_type = await latest_findings_by_type(db, domain.id)
    rating, _total, _failed = await compute_domain_rating(db, domain, findings_by_type=findings_by_type)
    if rating.insufficient_data or rating.score is None:
        return []
    if rating.score >= LOW_COMPLIANCE_SERIOUS_BELOW:
        return []
    severity = "critical" if rating.score < LOW_COMPLIANCE_CRITICAL_BELOW else "serious"
    # "Open DNS/authentication checks" alone doesn't say which check or why —
    # cite the actual finding behind whichever factor dragged the score down
    # the most, so the item is useful without a click.
    worst_factor = min(rating.factors, key=lambda f: f.score_pct, default=None)
    evidence = _worst_finding_summary(findings_by_type, worst_factor.factor) if worst_factor is not None else None
    return [
        ActionItem(
            severity=severity,
            category=CATEGORY_LOW_COMPLIANCE,
            title=f"{domain.name}: {rating.score}% compliance (grade {rating.grade})",
            action_hint="Open DNS/authentication checks",
            domain_id=str(domain.id),
            link_path=f"/domains/{domain.id}/dns",
            evidence=evidence,
        )
    ]


async def high_volume_failure(db: AsyncSession, domain: Domain, services: list[dict] | None = None) -> list[ActionItem]:
    """Shares compute_domain_rating's own windowed pass-rate computation
    (90-day rolling window, blocked-sender traffic excluded) via
    _windowed_totals, so this item's failing-message count never disagrees
    with what the rating itself is scoring. Names the sender behind most of
    the failures (from `services`, the same 90-day window) and links to it,
    so merge_sender_items folds it together with that sender's own items."""
    total, passed = await _windowed_totals(db, domain)
    failed = total - passed
    if total == 0 or failed < HIGH_VOLUME_FAILURE_MIN_COUNT:
        return []

    failed_pct = failed / total * 100
    if failed_pct < HIGH_VOLUME_FAILURE_MIN_PCT:
        return []

    severity = "critical" if failed_pct >= HIGH_VOLUME_FAILURE_CRITICAL_PCT else "serious"
    title = f"{domain.name}: {failed:,} failing messages ({round(failed_pct, 1)}%)"
    worst = max(
        (s for s in services or [] if s["dmarc_pass_pct"] is not None and s["dmarc_pass_pct"] < 100),
        key=lambda s: s["volume"] * (1 - s["dmarc_pass_pct"] / 100),
        default=None,
    )
    if worst is None:
        return [
            ActionItem(
                severity=severity,
                category=CATEGORY_HIGH_VOLUME_FAILURE,
                title=title,
                action_hint="Review Senders for the worst sender",
                domain_id=str(domain.id),
                link_path=f"/domains/{domain.id}/senders",
            )
        ]
    return [
        ActionItem(
            severity=severity,
            category=CATEGORY_HIGH_VOLUME_FAILURE,
            title=f'{title}, most from "{worst["service_label"]}"',
            action_hint=f'"{worst["service_label"]}" passes DMARC on {worst["dmarc_pass_pct"]}% of its mail',
            domain_id=str(domain.id),
            link_path=_sender_link(domain.id, worst["service_label"]),
        )
    ]


async def trending_down(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """The stored trend says the pass rate dropped for real (see
    app/services/rating/trend.py: significant, material and persistent)."""
    trend = await get_trend(db, domain.id)
    if trend is None or trend.state != "down":
        return []
    return [
        ActionItem(
            severity="serious",
            category=CATEGORY_HIGH_VOLUME_FAILURE,
            title=f"{domain.name}: pass rate down from {trend.baseline_pass_pct}% to {trend.recent_pass_pct}% this week",
            action_hint="Check which senders started failing",
            domain_id=str(domain.id),
            link_path=f"/domains/{domain.id}/senders",
        )
    ]


def merge_sender_items(items: list[ActionItem]) -> list[ActionItem]:
    """One item per sender: everything the rules found about the same sender
    (failing messages, alignment, unreviewed, likely spoofed) becomes a
    single item, titled by the most urgent one, its hints joined. Before
    this, one misaligned sender could show up as two to four items with
    different numbers. Items not about a sender pass through unchanged."""
    order = {"critical": 0, "serious": 1, "warning": 2, "good": 3, "neutral": 4}
    by_sender: dict[str, list[ActionItem]] = {}
    merged: list[ActionItem] = []
    for item in items:
        if item.link_path and "/senders?highlight=" in item.link_path:
            by_sender.setdefault(item.link_path, []).append(item)
        else:
            merged.append(item)
    for group in by_sender.values():
        group.sort(key=lambda i: (order.get(i.severity, 5), i.category))
        lead = group[0]
        hints = list(dict.fromkeys(i.action_hint for i in group if i.action_hint))
        merged.append(dataclasses.replace(lead, action_hint=" · ".join(hints)))
    return merged


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
                action_hint=f"{spf_pct}% SPF / {dkim_pct}% DKIM aligned: fix its SPF/DKIM alignment",
                domain_id=str(domain.id),
                link_path=_sender_link(domain.id, s["service_label"]),
            )
        )
    return items


async def spf_lookup_limit_risk(db: AsyncSession, domain: Domain) -> list[ActionItem]:
    """Directly surfaces the existing SPF near/over-limit finding
    (app/services/dns_checks/spf.py) from the last check run — no new
    checker logic, just reads what's already stored."""
    rows = await latest_dns_check_results_of_type_for_domain(db, domain.id, CheckType.spf)

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
                link_path=f"/domains/{domain.id}/dns",
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
            link_path=f"/domains/{domain.id}/dns?open=policy-builder",
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
            link_path=f"/domains/{domain.id}/dns?open=policy-builder",
        )
    ]
