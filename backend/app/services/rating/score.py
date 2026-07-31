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

from app.models.enums import CheckStatus, CheckType

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
    return CheckStatus.pass_


@dataclasses.dataclass
class RatingFactor:
    factor: str
    weight: int
    score_pct: float
    detail: str


@dataclasses.dataclass
class DomainRating:
    score: float | None
    grade: str | None
    insufficient_data: bool
    factors: list[RatingFactor]


def compute_rating(
    *,
    findings_by_type: dict[CheckType, list],
    dmarc_pass_count: int,
    total_message_count: int,
) -> DomainRating:
    """`findings_by_type` values are lists of objects with a `.status`
    (CheckStatus) attribute — either Finding instances (registry.run_all's
    return shape) or DnsCheckResult rows both satisfy this."""
    factors: list[RatingFactor] = []

    for check_type, weight_key in _DIRECT_CHECK_FACTORS:
        findings = findings_by_type.get(check_type) or []
        if not findings:
            continue
        status = worst_status([f.status for f in findings])
        factors.append(
            RatingFactor(factor=weight_key, weight=WEIGHTS[weight_key], score_pct=_STATUS_SCORE[status], detail=status.value)
        )

    dmarc_findings = findings_by_type.get(CheckType.dmarc) or []
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

    insufficient_data = total_message_count == 0
    if not insufficient_data:
        pass_pct = round(dmarc_pass_count / total_message_count * 100, 1)
        factors.append(
            RatingFactor(
                factor="dmarc_pass_rate", weight=WEIGHTS["dmarc_pass_rate"], score_pct=pass_pct, detail=f"{pass_pct}% of messages"
            )
        )

    if insufficient_data or not factors:
        return DomainRating(score=None, grade=None, insufficient_data=True, factors=factors)

    total_weight = sum(f.weight for f in factors)
    weighted_score = sum(f.weight * f.score_pct for f in factors) / total_weight

    return DomainRating(
        score=round(weighted_score, 1),
        grade=grade_for_score(weighted_score),
        insufficient_data=False,
        factors=factors,
    )
