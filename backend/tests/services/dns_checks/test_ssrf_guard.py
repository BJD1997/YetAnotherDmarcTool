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
