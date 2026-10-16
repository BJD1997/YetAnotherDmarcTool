import re

from app.services.organizations.slug import random_slug, slugify, unique_slug


def test_slugify_keeps_letters_digits_and_single_dashes():
    assert slugify("  Acme B.V. — Mail ") == "acme-b-v-mail"
    assert slugify("Ünïcode Org") == "unicode-org"
    assert slugify("!!!") == "org"
    assert len(slugify("x" * 200)) == 63


def test_unique_slug_adds_a_number_when_taken():
    assert unique_slug("Acme", set()) == "acme"
    assert unique_slug("Acme", {"acme", "acme-2"}) == "acme-3"


def test_random_slugs_are_long_and_differ():
    slugs = {random_slug() for _ in range(50)}
    assert len(slugs) == 50
    assert all(re.fullmatch(r"[a-z2-9]{10}", s) for s in slugs)
