"""compute_trend: a domain trends down only when the drop is statistically
real (z >= 1.96), material (>= 2 points) and persistent (>= 3 days below)."""

from datetime import date, timedelta

from app.services.rating.trend import DayCounts, compute_trend

TODAY = date(2026, 10, 2)


def series(baseline: tuple[int, int], recent: list[tuple[int, int]]) -> list[DayCounts]:
    """baseline=(total, passed) for each of the 28 baseline days; recent =
    (total, passed) for each of the 7 recent days, oldest first."""
    days = [DayCounts(TODAY - timedelta(days=34 - i), *baseline) for i in range(28)]
    days += [DayCounts(TODAY - timedelta(days=6 - i), total, passed) for i, (total, passed) in enumerate(recent)]
    return days


def test_quiet_server_one_bad_day_is_stable():
    result = compute_trend(series((4, 4), [(4, 4)] * 6 + [(4, 2)]), TODAY)
    assert result.state == "stable"
    assert result.days_below == 1


def test_quiet_server_failing_for_several_reports_trends_down():
    result = compute_trend(series((4, 4), [(4, 4)] * 4 + [(4, 2)] * 3), TODAY)
    assert result.state == "down"
    assert result.days_below == 3


def test_chatty_server_one_day_dip_is_stable():
    assert compute_trend(series((400, 400), [(400, 400)] * 6 + [(400, 280)]), TODAY).state == "stable"


def test_chatty_server_persistent_drop_trends_down():
    result = compute_trend(series((400, 400), [(400, 400)] * 2 + [(400, 360)] * 5), TODAY)
    assert result.state == "down"
    assert (result.baseline_pass_pct, result.recent_pass_pct) == (100.0, 92.9)


def test_drop_under_two_points_is_stable_however_significant():
    assert compute_trend(series((1000, 999), [(1000, 997)] * 7), TODAY).state == "stable"


def test_recovery_trends_up():
    assert compute_trend(series((100, 85), [(100, 99)] * 7), TODAY).state == "up"


def test_too_little_mail_is_insufficient_data():
    assert compute_trend(series((4, 4), [(0, 0)] * 5 + [(5, 5), (5, 5)]), TODAY).state == "insufficient_data"
