"""STARTTLS_CHECK_ENABLED: where outbound port 25 is blocked (Azure) the
probe can't succeed, so it's switched off — and then it must neither run nor
count against the domain's grade."""

import pytest

from app.config import settings
from app.models.enums import CheckStatus, CheckType
from app.services.dns_checks import registry
from app.services.dns_checks.base import Finding
from app.services.rating.score import compute_rating


@pytest.fixture
def stubbed_checks(monkeypatch):
    async def _ok(*args, **kwargs):
        return [Finding(status="pass", summary="ok")]

    async def _probe(*args, **kwargs):
        raise AssertionError("the port-25 probe must not run when switched off")

    async def _no_policy(*args, **kwargs):
        return "reject"

    monkeypatch.setattr(registry, "resolve_effective_policy", _no_policy)
    for module in (registry.spf, registry.dkim, registry.dmarc, registry.dmarcbis, registry.mx,
                   registry.mta_sts, registry.dane, registry.tls_rpt_check):
        monkeypatch.setattr(module, "check", _ok)
    monkeypatch.setattr(registry.starttls, "check", _probe)


async def test_starttls_switched_off_is_not_probed(stubbed_checks, monkeypatch):
    monkeypatch.setattr(settings, "starttls_check_enabled", False)

    results = await registry.run_all("example.com", [])

    assert results[CheckType.starttls] == []


def test_a_check_without_findings_is_left_out_of_the_grade():
    class _F:
        def __init__(self, status):
            self.status = status

    passing = {t: [_F(CheckStatus.pass_)] for t in (CheckType.spf, CheckType.dkim, CheckType.mx, CheckType.dmarc)}
    with_error = {**passing, CheckType.starttls: [_F(CheckStatus.error)]}
    switched_off = {**passing, CheckType.starttls: []}

    graded = lambda f: compute_rating(findings_by_type=f, dmarc_pass_count=10, total_message_count=10).score  # noqa: E731
    assert graded(switched_off) > graded(with_error)
    assert "starttls" not in {x.factor for x in compute_rating(findings_by_type=switched_off, dmarc_pass_count=10, total_message_count=10).factors}


@pytest.mark.parametrize(
    "mode, enabled, expected",
    [("probe", True, "probe"), ("tls_rpt", True, "tls_rpt"), ("off", True, "off"), ("probe", False, "off"), ("tls_rpt", False, "off")],
)
def test_effective_mode(monkeypatch, mode, enabled, expected):
    monkeypatch.setattr(settings, "starttls_check_mode", mode)
    monkeypatch.setattr(settings, "starttls_check_enabled", enabled)
    assert settings.effective_starttls_mode == expected


async def test_tls_rpt_mode_does_not_probe(stubbed_checks, monkeypatch):
    monkeypatch.setattr(settings, "starttls_check_mode", "tls_rpt")
    assert (await registry.run_all("example.com", []))[CheckType.starttls] == []
