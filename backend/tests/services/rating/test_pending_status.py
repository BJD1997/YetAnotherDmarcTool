from app.models.enums import CheckStatus, CheckType
from app.services.rating.score import compute_rating, worst_status


class _F:
    def __init__(self, status):
        self.status = status


def test_worst_status_ranks_pending_below_pass():
    assert worst_status([CheckStatus.pending, CheckStatus.pass_]) == CheckStatus.pass_
    assert worst_status([CheckStatus.pending]) == CheckStatus.pending


def test_pending_findings_are_left_out_of_the_grade():
    base = {t: [_F(CheckStatus.pass_)] for t in (CheckType.spf, CheckType.dkim, CheckType.mx, CheckType.dmarc)}
    pending = {**base, CheckType.starttls: [_F(CheckStatus.pending)]}
    rating = compute_rating(findings_by_type=pending, dmarc_pass_count=10, total_message_count=10)
    assert "starttls" not in {f.factor for f in rating.factors}
    assert rating.score == compute_rating(findings_by_type=base, dmarc_pass_count=10, total_message_count=10).score
