"""The name in an organization's direct sign-in link, /login/<slug>."""

import secrets

_RANDOM_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # no 0/o, 1/l look-alikes


def random_slug() -> str:
    """The default: 10 characters (about 50 bits), so organizations' links
    can't be guessed from their names."""
    return "".join(secrets.choice(_RANDOM_ALPHABET) for _ in range(10))
