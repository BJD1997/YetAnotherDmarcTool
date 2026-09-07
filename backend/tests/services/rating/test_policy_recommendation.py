from app.models.domain import Domain
from app.models.enums import DomainMailProfile
from app.services.rating.domain_rating import PolicyReadiness
from app.services.rating.policy_recommendation import build_policy_recommendation
from app.services.rating.score import DomainRating, RatingFactor


def _domain(mail_profile=DomainMailProfile.sends_mail) -> Domain:
    return Domain(name="example.com", mail_profile=mail_profile)


def _rating(insufficient_data=False, pass_rate_pct=None) -> DomainRating:
    factors = []
    if pass_rate_pct is not None:
        factors.append(RatingFactor(factor="dmarc_pass_rate", weight=40, score_pct=pass_rate_pct, detail=""))
    return DomainRating(score=None, grade=None, insufficient_data=insufficient_data, factors=factors)


def _readiness(latest_policy=None) -> PolicyReadiness:
    return PolicyReadiness(
        eligible=True, ready=False, latest_policy=latest_policy, next_rung=None, pass_rate_pct=None, total_volume=0
    )


def test_receive_only_domain_recommends_reject_regardless_of_data():
    domain = _domain(mail_profile=DomainMailProfile.receive_only)
    rec = build_policy_recommendation(
        domain=domain, rating=_rating(insufficient_data=True), total=0, readiness=_readiness(), stability_days=0, blockers=[]
    )
    assert rec["policy"] == "reject"
    assert rec["blocked"] is False
    assert rec["np"] == "reject"


def test_parked_domain_recommends_reject():
    domain = _domain(mail_profile=DomainMailProfile.parked)
    rec = build_policy_recommendation(
        domain=domain, rating=_rating(insufficient_data=True), total=0, readiness=_readiness(), stability_days=0, blockers=[]
    )
    assert rec["policy"] == "reject"
    assert "not used for mail" in rec["reasoning"]


def test_insufficient_data_recommends_monitor_only():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(insufficient_data=True), total=0, readiness=_readiness(), stability_days=0, blockers=[]
    )
    assert rec["policy"] == "none"
    assert rec["blocked"] is False
    assert rec["np"] is None


def test_blockers_present_reports_top_offender_and_stays_put():
    blockers = [
        {"service_label": "shadow-it.example", "volume": 500},
        {"service_label": "small-sender.example", "volume": 10},
    ]
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.0), total=1000, readiness=_readiness(latest_policy="none"),
        stability_days=0, blockers=blockers,
    )
    assert rec["blocked"] is True
    assert "shadow-it.example" in rec["blocking_reason"]
    assert "1 other unreviewed sender" in rec["blocking_reason"]
    assert rec["policy"] == "none"


def test_low_pass_rate_blocks_with_fail_count_in_reasoning():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=80.0), total=100, readiness=_readiness(latest_policy="none"),
        stability_days=0, blockers=[],
    )
    assert rec["blocked"] is True
    assert "20 of 100 messages" in rec["reasoning"]
    assert rec["policy"] == "none"


def test_ready_domain_at_none_recommends_quarantine():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.5), total=1000, readiness=_readiness(latest_policy="none"),
        stability_days=0, blockers=[],
    )
    assert rec["policy"] == "quarantine"
    assert rec["blocked"] is False
    assert rec["np"] == "reject"


def test_quarantine_not_yet_stable_stays_at_quarantine():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.5), total=1000, readiness=_readiness(latest_policy="quarantine"),
        stability_days=5, blockers=[],
    )
    assert rec["policy"] == "quarantine"
    assert "5 day(s)" in rec["reasoning"]


def test_quarantine_stable_long_enough_recommends_reject():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.5), total=1000, readiness=_readiness(latest_policy="quarantine"),
        stability_days=14, blockers=[],
    )
    assert rec["policy"] == "reject"
    assert rec["blocked"] is False


def test_already_at_reject_has_nothing_further_to_recommend():
    rec = build_policy_recommendation(
        domain=_domain(), rating=_rating(pass_rate_pct=99.9), total=1000, readiness=_readiness(latest_policy="reject"),
        stability_days=60, blockers=[],
    )
    assert rec["policy"] == "reject"
    assert "nothing further" in rec["reasoning"]
    assert rec["np"] == "reject"
