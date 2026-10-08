import re

from app.services.dns_checks.starttls_tls_rpt import evaluate


def test_not_reaching_the_app_gives_no_finding():
    for status in ("not_configured", "no_rua", "points_elsewhere", "lookup_error", "no_mailbox"):
        assert evaluate(status, 0, 0, []) == []


def test_waiting_for_reports_is_pending():
    [f] = evaluate("correct", 0, 0, [])
    assert f.status == "pending" and "Waiting for TLS-RPT reports" in f.summary


def test_pass_at_or_under_two_percent():
    [f] = evaluate("correct", 980, 20, [{"result_type": "certificate-expired", "failed_session_count": 20, "receiving_mx_hostname": "mx1.example.com"}])
    assert f.status == "pass"


def test_warn_above_two_percent_names_hosts_and_types():
    [f] = evaluate("correct", 90, 10, [{"result_type": "starttls-not-supported", "failed_session_count": 10, "receiving_mx_hostname": "mx2.example.com"}])
    assert f.status == "warn"
    assert re.search(r"\bmx2\.example\.com\b", f.summary) and "starttls-not-supported" in f.summary


def test_fail_when_no_session_succeeded():
    [f] = evaluate("correct", 0, 5, [{"result_type": "certificate-host-mismatch", "failed_session_count": 5}])
    assert f.status == "fail"
