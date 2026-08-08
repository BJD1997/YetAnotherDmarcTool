import pytest

from app.services.source_identification.registrable_domain import registrable_domain


@pytest.mark.parametrize(
    "hostname,expected",
    [
        ("example.com", "example.com"),
        ("web01.example.com", "example.com"),
        ("deep.sub.web01.example.com", "example.com"),
        ("example.co.uk", "example.co.uk"),
        ("web01.example.co.uk", "example.co.uk"),
        ("com", "com"),
        ("EXAMPLE.COM", "example.com"),
        ("web01.example.com.", "example.com"),
    ],
)
def test_registrable_domain(hostname, expected):
    assert registrable_domain(hostname) == expected
