"""Domain rating: synthesizes existing DNS-check statuses + observed DMARC
pass rate into a single 0-100 score + letter grade. A first-pass weighting,
explicitly meant to be eyeballed against real domains and adjusted rather
than treated as final — see the transparent per-factor breakdown returned
alongside the score.

BIMI and standalone DNSSEC have no backing checker anywhere in this app
(adding one would be new checker logic, out of scope for what is otherwise
a pure presentation feature) — they are never scored here; the caller
shows them as "not checked" rather than this module fabricating a status."""

import dataclasses

from app.models.enums import CheckStatus, CheckType, DomainMailProfile

WEIGHTS: dict[str, int] = {
    "dmarc_policy": 25,
    "dmarc_pass_rate": 20,
    "spf": 10,
    "dkim": 10,
    "mx": 10,
    "starttls": 10,
    "mta_sts": 5,
    "dane": 5,
    "tls_rpt": 5,
}
assert sum(WEIGHTS.values()) == 100

GRADE_BANDS = [(90, "A"), (80, "B"), (70, "C"), (60, "D")]

_STATUS_SCORE = {CheckStatus.pass_: 100, CheckStatus.warn: 60, CheckStatus.error: 40, CheckStatus.fail: 0}

# (CheckType, WEIGHTS key) pairs scored directly from "worst finding of that type" —
# dmarc_policy/dmarc_pass_rate are handled separately since dmarc_policy reuses the
# dmarc CheckType's findings under a differently-weighted key, and dmarc_pass_rate
# isn't a check finding at all but observed traffic.
_DIRECT_CHECK_FACTORS = [
    (CheckType.spf, "spf"),
    (CheckType.dkim, "dkim"),
    (CheckType.mx, "mx"),
    (CheckType.starttls, "starttls"),
    (CheckType.mta_sts, "mta_sts"),
    (CheckType.dane, "dane"),
    (CheckType.tls_rpt, "tls_rpt"),
]


def grade_for_score(score: float) -> str:
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def worst_status(statuses: list[CheckStatus]) -> CheckStatus:
    if CheckStatus.fail in statuses:
        return CheckStatus.fail
    if CheckStatus.error in statuses:
        return CheckStatus.error
    if CheckStatus.warn in statuses:
        return CheckStatus.warn
    if CheckStatus.pending in statuses and CheckStatus.pass_ not in statuses:
        return CheckStatus.pending
    return CheckStatus.pass_


def tally_worst_status(findings_by_type: dict[CheckType, list]) -> dict[str, int]:
    """One vote per check_type (its worst finding), not one per finding —
    a category-level "how many of your checks are in each state" tally for
    a compact per-domain summary, e.g. the Domains list page, rather than
    the full per-finding detail Best Practices shows."""
    counts = {status.value: 0 for status in CheckStatus}
    for findings in findings_by_type.values():
        counts[worst_status([f.status for f in findings]).value] += 1
    return counts


@dataclasses.dataclass
class RatingFactor:
    factor: str
    weight: int
    score_pct: float
    detail: str


@dataclasses.dataclass
class PartRating:
    """One half of the rating: setup (the DNS checks) or delivery (how the
    domain's mail does at receivers, from DMARC reports)."""

    score: float
    grade: str
    factors: list[RatingFactor]


@dataclasses.dataclass
class DomainRating:
    score: float | None
    grade: str | None
    insufficient_data: bool
    factors: list[RatingFactor]
    # Each is None until it has something to rate: setup until the first
    # DNS check, delivery until the first report (always, for a domain
    # that doesn't send mail).
    setup: PartRating | None = None
    delivery: PartRating | None = None


def _weighted(factors: list[RatingFactor]) -> float:
    return sum(f.weight * f.score_pct for f in factors) / sum(f.weight for f in factors)


def _part(factors: list[RatingFactor]) -> PartRating | None:
    if not factors:
        return None
    score = _weighted(factors)
    return PartRating(score=round(score, 1), grade=grade_for_score(score), factors=factors)


def _pct(count: int, total: int) -> float:
    return round(count / total * 100, 1)


def compute_rating(
    *,
    findings_by_type: dict[CheckType, list],
    dmarc_pass_count: int,
    total_message_count: int,
    mail_profile: DomainMailProfile = DomainMailProfile.sends_mail,
    spf_pass_count: int | None = None,
    dkim_pass_count: int | None = None,
) -> DomainRating:
    """`findings_by_type` values are lists of objects with a `.status`
    (CheckStatus) attribute — either Finding instances (registry.run_all's
    return shape) or DnsCheckResult rows both satisfy this."""
    factors: list[RatingFactor] = []

    for check_type, weight_key in _DIRECT_CHECK_FACTORS:
        # Pending findings (no result yet) don't count either way.
        findings = [f for f in findings_by_type.get(check_type) or [] if f.status != CheckStatus.pending]
        if not findings:
            continue
        status = worst_status([f.status for f in findings])
        factors.append(
            RatingFactor(factor=weight_key, weight=WEIGHTS[weight_key], score_pct=_STATUS_SCORE[status], detail=status.value)
        )

    dmarc_findings = [f for f in findings_by_type.get(CheckType.dmarc) or [] if f.status != CheckStatus.pending]
    if dmarc_findings:
        policy_status = worst_status([f.status for f in dmarc_findings])
        factors.append(
            RatingFactor(
                factor="dmarc_policy",
                weight=WEIGHTS["dmarc_policy"],
                score_pct=_STATUS_SCORE[policy_status],
                detail=policy_status.value,
            )
        )

    # dmarc_pass_rate measures "are my legitimate senders authenticating
    # correctly" — meaningless for a domain with no legitimate senders at
    # all (receive_only/parked, see Domain.mail_profile). Any traffic a
    # domain like that sees is illegitimate by definition, so a low (even
    # 0%) pass rate reflects spoofing being correctly rejected, not a
    # problem — scored the same way the DNS checks that also don't apply
    # here (starttls/dane/mta_sts/tls_rpt) already are: left out of the
    # weighted average rather than penalizing what's actually correct
    # behavior. Message volume also stops gating insufficient_data in that
    # case — zero traffic ever is the expected steady state, not a data
    # shortfall (the `not factors` fallback below still catches the case
    # where there's truly nothing to rate, e.g. no DNS checks have run yet).
    if mail_profile == DomainMailProfile.sends_mail:
        insufficient_data = total_message_count == 0
        if not insufficient_data:
            pass_pct = round(dmarc_pass_count / total_message_count * 100, 1)
            factors.append(
                RatingFactor(
                    factor="dmarc_pass_rate", weight=WEIGHTS["dmarc_pass_rate"], score_pct=pass_pct, detail=f"{pass_pct}% of messages"
                )
            )
    else:
        insufficient_data = False

    setup = _part([f for f in factors if f.factor != "dmarc_pass_rate"])
    delivery = None
    pass_rate = next((f for f in factors if f.factor == "dmarc_pass_rate"), None)
    if pass_rate is not None:
        # Scored on the DMARC pass rate alone; SPF and DKIM are shown as
        # its detail (DMARC passes when either does).
        detail = [dataclasses.replace(pass_rate, weight=100, detail=f"{pass_rate.score_pct}% of {total_message_count:,} messages")]
        for factor, count in (("spf_pass_rate", spf_pass_count), ("dkim_pass_rate", dkim_pass_count)):
            if count is not None:
                pct = _pct(count, total_message_count)
                detail.append(RatingFactor(factor=factor, weight=0, score_pct=pct, detail=f"{pct}%"))
        delivery = PartRating(score=pass_rate.score_pct, grade=grade_for_score(pass_rate.score_pct), factors=detail)

    if insufficient_data or not factors:
        return DomainRating(score=None, grade=None, insufficient_data=True, factors=factors, setup=setup, delivery=delivery)

    weighted_score = _weighted(factors)
    return DomainRating(
        score=round(weighted_score, 1),
        grade=grade_for_score(weighted_score),
        insufficient_data=False,
        factors=factors,
        setup=setup,
        delivery=delivery,
    )
