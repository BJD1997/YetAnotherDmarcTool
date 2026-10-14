"""Per-domain DMARC pass-rate trend: the last 7 days against the 28 before.

A domain only trends down (or up) when the change is all four of:
- real, not chance: a one-sided test at the 5% level that weighs message
  counts (2 failures out of 28 weigh far less than 200 out of 2,800). Under
  EXACT_TEST_BELOW messages a week it's Fisher's exact test: the usual
  z-test overstates certainty when there are only a handful of messages;
- material: at least 2 percentage points;
- more than noise in absolute terms: at least 3 more failed (or passed)
  messages than the baseline rate predicts, so a domain with ~2 messages a
  day, where one failure moves the rate by 7 points, isn't flagged by a
  swing of one or two messages;
- persistent: at least 3 distinct days in the last 7 (with mail) below the
  baseline rate, so one bad day never counts, however busy the server.

Pure: callers pass per-day counts (see repositories.dmarc_reports.
daily_totals_excluding_blocked) and store the result (domain_trends)."""

import dataclasses
import math
from datetime import date, timedelta
from typing import Literal

RECENT_DAYS = 7
BASELINE_DAYS = 28
Z_THRESHOLD = 1.96
P_THRESHOLD = 0.05
EXACT_TEST_BELOW = 1000
MIN_CHANGE_POINTS = 2.0
MIN_EXCESS_MESSAGES = 3
MIN_DAYS = 3
MIN_RECENT_MESSAGES = 10
MIN_BASELINE_MESSAGES = 20

TrendState = Literal["up", "stable", "down", "insufficient_data"]


@dataclasses.dataclass(frozen=True)
class DayCounts:
    day: date
    total: int
    passed: int


@dataclasses.dataclass(frozen=True)
class TrendResult:
    state: TrendState
    recent_pass_pct: float | None
    baseline_pass_pct: float | None
    recent_messages: int
    baseline_messages: int
    days_below: int
    days_above: int


def _z(base_passed: int, base_total: int, recent_passed: int, recent_total: int) -> float:
    """Positive when the recent rate is lower than the baseline."""
    p_base = base_passed / base_total
    p_recent = recent_passed / recent_total
    p_pool = (base_passed + recent_passed) / (base_total + recent_total)
    variance = p_pool * (1 - p_pool) * (1 / base_total + 1 / recent_total)
    if variance <= 0:
        return 0.0
    return (p_base - p_recent) / math.sqrt(variance)


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _fisher_one_sided(base_passed: int, base_total: int, recent_passed: int, recent_total: int, lower: bool) -> float:
    """P(the recent week passes this few (lower) or this many messages) if
    it had the same pass rate as the baseline: the hypergeometric tail."""
    population = base_total + recent_total
    passed_total = base_passed + recent_passed
    lo = max(0, recent_total - (population - passed_total))
    hi = min(recent_total, passed_total)
    xs = range(lo, recent_passed + 1) if lower else range(recent_passed, hi + 1)
    denominator = _log_comb(population, recent_total)
    return min(
        1.0,
        sum(
            math.exp(_log_comb(passed_total, x) + _log_comb(population - passed_total, recent_total - x) - denominator)
            for x in xs
        ),
    )


def _significant(base_passed: int, base_total: int, recent_passed: int, recent_total: int, lower: bool) -> bool:
    if recent_total < EXACT_TEST_BELOW:
        return _fisher_one_sided(base_passed, base_total, recent_passed, recent_total, lower) < P_THRESHOLD
    z = _z(base_passed, base_total, recent_passed, recent_total)
    return z >= Z_THRESHOLD if lower else z <= -Z_THRESHOLD


def compute_trend(days: list[DayCounts], today: date) -> TrendResult:
    recent_start = today - timedelta(days=RECENT_DAYS - 1)
    baseline_start = recent_start - timedelta(days=BASELINE_DAYS)
    recent = [d for d in days if recent_start <= d.day <= today and d.total > 0]
    baseline = [d for d in days if baseline_start <= d.day < recent_start and d.total > 0]

    recent_total = sum(d.total for d in recent)
    recent_passed = sum(d.passed for d in recent)
    base_total = sum(d.total for d in baseline)
    base_passed = sum(d.passed for d in baseline)
    recent_pct = round(recent_passed / recent_total * 100, 1) if recent_total else None
    base_pct = round(base_passed / base_total * 100, 1) if base_total else None

    if recent_total < MIN_RECENT_MESSAGES or base_total < MIN_BASELINE_MESSAGES or len(recent) < MIN_DAYS:
        return TrendResult("insufficient_data", recent_pct, base_pct, recent_total, base_total, 0, 0)

    p_base = base_passed / base_total
    days_below = sum(1 for d in recent if d.passed / d.total < p_base)
    days_above = sum(1 for d in recent if d.passed / d.total > p_base)
    change = (recent_passed / recent_total - p_base) * 100
    # Passed messages more (+) or fewer (-) than the baseline rate predicts.
    excess = recent_passed - p_base * recent_total

    if (
        change <= -MIN_CHANGE_POINTS
        and -excess >= MIN_EXCESS_MESSAGES
        and days_below >= MIN_DAYS
        and _significant(base_passed, base_total, recent_passed, recent_total, lower=True)
    ):
        state: TrendState = "down"
    elif (
        change >= MIN_CHANGE_POINTS
        and excess >= MIN_EXCESS_MESSAGES
        and days_above >= MIN_DAYS
        and _significant(base_passed, base_total, recent_passed, recent_total, lower=False)
    ):
        state = "up"
    else:
        state = "stable"
    return TrendResult(state, recent_pct, base_pct, recent_total, base_total, days_below, days_above)
