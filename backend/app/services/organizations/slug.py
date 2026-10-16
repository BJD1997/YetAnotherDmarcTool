"""The short name in an organization's direct sign-in link, /login/<slug>."""

import re
import unicodedata

_NOT_ALLOWED = re.compile(r"[^a-z0-9]+")
MAX_LENGTH = 63


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = _NOT_ALLOWED.sub("-", ascii_name.lower()).strip("-")[:MAX_LENGTH].rstrip("-")
    return slug or "org"


def unique_slug(name: str, taken: set[str]) -> str:
    base = slugify(name)
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: MAX_LENGTH - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        n += 1
