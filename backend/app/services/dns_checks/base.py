"""Every checker (spf.py, dkim.py, dmarc.py, dmarcbis.py, mx.py, mta_sts.py,
dane.py) returns a list of these — a common shape is what lets
dns_check_results stay one generic table despite very different per-check-
type payloads (see the model's docstring)."""

from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["pass", "warn", "fail", "error"]

# Bumped whenever a checker's judgement logic changes meaningfully, so old
# rows in dns_check_results can be told apart from ones evaluated under a
# newer rule set (most relevant for the DMARCbis module in Phase 4, since
# that's still tracking an IETF draft).
RULE_VERSION = "2026-07-31"


@dataclass
class Finding:
    status: Status
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    # e.g. a DKIM selector name, or a specific MX hostname for a per-host finding.
    subject: str | None = None
    rule_version: str = RULE_VERSION


def is_null_mx(mx_records: list[tuple[int, str]]) -> bool:
    """True for an explicit RFC 7505 null MX ("0 ."). resolve_mx()
    (dns_checks/resolver.py) strips every exchange hostname's trailing dot
    for comparability elsewhere (CNAME/A-AAAA lookups need bare names) —
    for a null MX the exchange *is* the DNS root name, so that stripping
    collapses it to "", not the literal ".". Shared by mx.py/starttls.py/
    dane.py so this isn't re-derived (and re-broken, as it was — checked
    against "." in all three, which never matched) a fourth time."""
    return len(mx_records) == 1 and mx_records[0][1] == ""
