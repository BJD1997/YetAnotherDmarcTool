import pytest

from app.services.dns_checks.ssrf_guard import _is_public


@pytest.mark.parametrize(
    "ip,expected_public",
    [
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("127.0.0.1", False),           # loopback
        ("10.0.0.5", False),            # private
        ("172.28.0.5", False),          # the compose internal subnet
        ("192.168.1.10", False),        # private
        ("169.254.169.254", False),     # link-local / cloud metadata
        ("0.0.0.0", False),             # unspecified
        ("224.0.0.1", False),           # multicast
        ("::1", False),                 # v6 loopback
        ("fe80::1", False),             # v6 link-local
        ("fc00::1", False),             # v6 unique-local (private)
        ("2606:4700:4700::1111", True), # public v6
        ("::ffff:10.0.0.1", False),     # v4-mapped private
        ("::ffff:8.8.8.8", True),       # v4-mapped public
    ],
)
def test_is_public(ip: str, expected_public: bool) -> None:
    assert _is_public(ip) is expected_public


@pytest.mark.parametrize(
    "address",
    [
        "100.64.0.1",  # carrier-grade NAT (Tailscale's range)
        "192.0.0.8",
        "198.18.0.1",  # benchmarking
        "64:ff9b::a00:1",  # NAT64 wrapping 10.0.0.1
        "64:ff9b:1::1",  # local-use NAT64
        "2002:a00:1::1",  # 6to4 wrapping 10.0.0.1
        "2001:0:4136:e378:8000:63bf:f5ff:fffe",  # Teredo wrapping 10.0.0.1
        "::ffff:127.0.0.1",
    ],
)
def test_addresses_that_are_not_globally_routable_are_refused(address):
    assert _is_public(address) is False


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946", "64:ff9b::5db8:d822"])
def test_public_addresses_are_allowed(address):
    assert _is_public(address) is True
