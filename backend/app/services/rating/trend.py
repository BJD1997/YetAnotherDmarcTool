"""Per-domain DMARC pass-rate trend: the last 7 days against the 28 before.

A domain only trends down (or up) when the change is all three of:
- real, not chance: a one-sided two-proportion z-test, z >= 1.96, which
  takes message counts into account (2 failures out of 28 weigh far less
  than 200 out of 2,800);
- material: at least 2 percentage points;
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
MIN_CHANGE_POINTS = 2.0
MIN_DAYS = 3
MIN_MESSAGES = 20

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

    if recent_total < MIN_MESSAGES or base_total < MIN_MESSAGES or len(recent) < MIN_DAYS:
        return TrendResult("insufficient_data", recent_pct, base_pct, recent_total, base_total, 0, 0)

    p_base = base_passed / base_total
    days_below = sum(1 for d in recent if d.passed / d.total < p_base)
    days_above = sum(1 for d in recent if d.passed / d.total > p_base)
    z = _z(base_passed, base_total, recent_passed, recent_total)
    change = (recent_passed / recent_total - p_base) * 100

    if z >= Z_THRESHOLD and change <= -MIN_CHANGE_POINTS and days_below >= MIN_DAYS:
        state: TrendState = "down"
    elif z <= -Z_THRESHOLD and change >= MIN_CHANGE_POINTS and days_above >= MIN_DAYS:
        state = "up"
    else:
        state = "stable"
    return TrendResult(state, recent_pct, base_pct, recent_total, base_total, days_below, days_above)
