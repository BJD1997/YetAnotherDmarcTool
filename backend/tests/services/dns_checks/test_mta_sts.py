"""RFC 8461 §4.1: a leading "*." wildcard in an mx: pattern matches exactly
one label, not "one or more" — this is the exact bug found against a real
Microsoft 365 customer domain during development (see _mx_covered's own
docstring), where mx: *.mx.microsoft was wrongly treated as covering a
two-label-deeper real MX host."""

from app.services.dns_checks.mta_sts import _mx_covered


def test_wildcard_matches_exactly_one_label():
    assert _mx_covered("foo.mx.microsoft", ["*.mx.microsoft"]) is True


def test_wildcard_does_not_match_two_labels_deep():
    # the real-world case: a wildcard that looked like it should cover this,
    # but RFC 8461 says otherwise — this must stay False.
    assert _mx_covered("foo.bar.mx.microsoft", ["*.mx.microsoft"]) is False


def test_exact_match_no_wildcard():
    assert _mx_covered("mail.example.com", ["mail.example.com"]) is True


def test_exact_match_fails_for_different_host():
    assert _mx_covered("mail.example.com", ["other.example.com"]) is False


def test_wildcard_requires_at_least_one_label_before_suffix():
    # mx.microsoft itself is the suffix, not something "*.mx.microsoft" covers
    assert _mx_covered("mx.microsoft", ["*.mx.microsoft"]) is False


def test_case_and_trailing_dot_insensitive():
    assert _mx_covered("Foo.MX.Microsoft.", ["*.mx.microsoft"]) is True
    assert _mx_covered("foo.mx.microsoft", ["*.MX.Microsoft."]) is True


def test_multiple_patterns_any_match_covers():
    assert _mx_covered("foo.bar.mx.microsoft", ["*.mx.microsoft", "*.bar.mx.microsoft"]) is True


def test_no_patterns_never_covered():
    assert _mx_covered("mail.example.com", []) is False
