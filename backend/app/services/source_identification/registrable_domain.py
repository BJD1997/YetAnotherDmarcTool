"""Minimal eTLD+1 fallback for when a hostname doesn't match any known
sending service (patterns.py) — used to fall back to "whose domain is this"
(e.g. "web01.example.com" -> "example.com") rather than showing a
raw hostname or IP. Deliberately NOT a real Public Suffix List: a proper PSL
library (e.g. tldextract) phones home for updates by default, which
conflicts with this project's established no-surprise-outbound-calls
posture (see parsedmarc_adapter.py's offline=True). This is a small,
hardcoded exception list good enough for a "roughly whose domain" label,
not a security-relevant boundary. (DMARC checks don't use it: they walk
the DNS tree like receivers do, see dmarc_record.tree_walk_targets.)"""

_TWO_LABEL_SUFFIXES = {
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "net.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "net.nz",
    "co.za", "org.za",
    "co.jp", "ne.jp", "or.jp", "ac.jp",
    "com.br", "net.br", "org.br",
    "co.in", "net.in", "org.in",
    "com.mx", "com.ar", "com.co", "com.pe", "com.uy",
    "co.kr", "or.kr",
    "com.cn", "net.cn", "org.cn", "com.hk", "com.tw", "com.sg", "com.my", "com.ph", "co.id", "co.th", "com.vn",
    "co.il", "com.tr", "com.sa", "com.eg",
    "com.ua", "com.pl",
    "co.ke", "com.ng",
}


def registrable_domain(hostname: str) -> str:
    labels = hostname.rstrip(".").lower().split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES and len(labels) > 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
