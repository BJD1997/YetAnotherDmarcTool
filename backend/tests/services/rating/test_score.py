import pytest

from app.models.enums import CheckType, DomainMailProfile
from app.services.dns_checks.base import Finding
from app.services.rating.score import WEIGHTS, compute_rating, grade_for_score, worst_status


def test_weights_sum_to_100():
    assert sum(WEIGHTS.values()) == 100


@pytest.mark.parametrize(
    "score,expected_grade",
    [(100, "A"), (90, "A"), (89.9, "B"), (80, "B"), (79.9, "C"), (70, "C"), (69.9, "D"), (60, "D"), (59.9, "F"), (0, "F")],
)
def test_grade_bands(score, expected_grade):
    assert grade_for_score(score) == expected_grade


def test_worst_status_fail_beats_everything():
    assert worst_status(["pass", "warn", "fail", "error"]) == "fail"


def test_worst_status_error_beats_warn_and_pass():
    assert worst_status(["pass", "warn", "error"]) == "error"


def test_worst_status_all_pass_is_pass():
    assert worst_status(["pass", "pass"]) == "pass"


def test_worst_status_empty_defaults_to_pass():
    assert worst_status([]) == "pass"


def test_no_findings_at_all_is_insufficient_data():
    rating = compute_rating(findings_by_type={}, dmarc_pass_count=0, total_message_count=0)
    assert rating.insufficient_data is True
    assert rating.score is None
    assert rating.grade is None


def test_sends_mail_with_zero_messages_is_insufficient_data_even_with_dns_findings():
    findings = {CheckType.spf: [Finding(status="pass", summary="ok")]}
    rating = compute_rating(
        findings_by_type=findings, dmarc_pass_count=0, total_message_count=0, mail_profile=DomainMailProfile.sends_mail
    )
    assert rating.insufficient_data is True


def test_parked_domain_with_zero_messages_is_not_insufficient_data():
    # a parked/receive_only domain legitimately sees no real senders — zero
    # traffic is the expected steady state, not a data shortfall.
    findings = {
        CheckType.spf: [Finding(status="pass", summary="ok")],
        CheckType.mx: [Finding(status="pass", summary="ok")],
    }
    rating = compute_rating(
        findings_by_type=findings, dmarc_pass_count=0, total_message_count=0, mail_profile=DomainMailProfile.parked
    )
    assert rating.insufficient_data is False
    assert rating.score is not None
    # dmarc_pass_rate must not be one of the scored factors for a non-sending domain
    assert not any(f.factor == "dmarc_pass_rate" for f in rating.factors)


def test_full_rating_computes_expected_weighted_score():
    findings = {
        CheckType.spf: [Finding(status="pass", summary="ok")],  # weight 10, 100%
        CheckType.dkim: [Finding(status="fail", summary="bad")],  # weight 10, 0%
        CheckType.dmarc: [Finding(status="pass", summary="ok")],  # dmarc_policy weight 25, 100%
    }
    rating = compute_rating(findings_by_type=findings, dmarc_pass_count=50, total_message_count=100)
    assert rating.insufficient_data is False
    # spf(10*100) + dkim(10*0) + dmarc_policy(25*100) + dmarc_pass_rate(20*50) / (10+10+25+20)
    expected = (10 * 100 + 10 * 0 + 25 * 100 + 20 * 50) / (10 + 10 + 25 + 20)
    assert rating.score == round(expected, 1)
    assert rating.grade == grade_for_score(expected)


def test_worst_finding_of_type_is_used_not_best():
    findings = {CheckType.spf: [Finding(status="pass", summary="ok"), Finding(status="fail", summary="bad")]}
    rating = compute_rating(findings_by_type=findings, dmarc_pass_count=0, total_message_count=1)
    spf_factor = next(f for f in rating.factors if f.factor == "spf")
    assert spf_factor.detail == "fail"
    assert spf_factor.score_pct == 0
