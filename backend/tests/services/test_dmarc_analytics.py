from app.services.dmarc_analytics import _fcrdns_status, _is_ipv6


def _ip(source_ip: str, fcrdns_valid):
    return {"source_ip": source_ip, "fcrdns_valid": fcrdns_valid}


def test_fcrdns_status_rollup():
    assert _fcrdns_status([_ip("1.1.1.1", True)]) == "pass"
    assert _fcrdns_status([_ip("1.1.1.1", True), _ip("2.2.2.2", True)]) == "pass"
    # any unconfirmed (bad PTR or no PTR) alongside a pass -> partial
    assert _fcrdns_status([_ip("1.1.1.1", True), _ip("2.2.2.2", False)]) == "partial"
    assert _fcrdns_status([_ip("1.1.1.1", True), _ip("2.2.2.2", None)]) == "partial"
    # nothing confirmed -> fail, whether bad PTR (False) or missing (None)
    assert _fcrdns_status([_ip("1.1.1.1", False)]) == "fail"
    assert _fcrdns_status([_ip("1.1.1.1", None)]) == "fail"
    assert _fcrdns_status([_ip("1.1.1.1", False), _ip("2.2.2.2", None)]) == "fail"


def test_is_ipv6():
    assert _is_ipv6("2001:db8::1") is True
    assert _is_ipv6("2a01:111:f403:c201::3") is True
    assert _is_ipv6("5.22.251.58") is False
    assert _is_ipv6("46.182.217.240") is False
