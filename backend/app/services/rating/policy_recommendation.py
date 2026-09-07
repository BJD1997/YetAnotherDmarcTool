"""Pure, DB-free policy-recommendation engine for the DMARC policy-builder
UI — takes already-computed rating/readiness/blocker data (from
domain_rating.py and action_queue/rules.py) and turns it into one concrete
next-policy recommendation with human-readable reasoning. No queries here;
this is business logic, same category as score.py's compute_rating."""

from app.models.domain import Domain
from app.models.enums import DomainMailProfile
from app.services.rating.domain_rating import READY_TO_ENFORCE_MIN_PASS_PCT, PolicyReadiness
from app.services.rating.score import DomainRating

POLICY_STABILITY_MIN_DAYS = 14


def build_policy_recommendation(
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
