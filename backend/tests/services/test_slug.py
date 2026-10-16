import re

from app.services.organizations.slug import random_slug


def test_random_slugs_are_long_and_differ():
    slugs = {random_slug() for _ in range(50)}
    assert len(slugs) == 50
    assert all(re.fullmatch(r"[a-z2-9]{10}", s) for s in slugs)
