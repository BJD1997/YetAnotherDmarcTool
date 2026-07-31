"""Minimal eTLD+1 fallback for when a hostname doesn't match any known
sending service (patterns.py) — used to fall back to "whose domain is this"
(e.g. "web01.davidshosting.nl" -> "davidshosting.nl") rather than showing a
raw hostname or IP. Deliberately NOT a real Public Suffix List: a proper PSL
library (e.g. tldextract) phones home for updates by default, which
conflicts with this project's established no-surprise-outbound-calls
posture (see parsedmarc_adapter.py's offline=True). This is a small,
hardcoded exception list good enough for a "roughly whose domain" label,
not a security-relevant boundary."""

_TWO_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au",
    "co.nz",
    "co.za",
    "co.jp", "ne.jp",
    "com.br",
    "co.in",
}


def registrable_domain(hostname: str) -> str:
    labels = hostname.rstrip(".").lower().split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES and len(labels) > 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
