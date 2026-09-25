"""Was a report email really sent by who it says? Anyone can mail a DMARC
reporting address, so a report's own claims (org_name, report_id) prove
nothing. What does: the receiving mailbox's own DMARC verdict on the email
itself, from the Authentication-Results header Exchange Online (EOP) stamps
on every inbound message.

Only the FIRST Authentication-Results header is read — the receiving server
prepends its own, so anything below it (including one a forger wrote into
their message) is not trusted.

A pass proves the email came from its From domain, not that that domain is
the reporter the report names: Yahoo, for instance, legitimately sends
reports from dmarc.yahoo.com that name yahooinc.com. So the authenticated
domain is recorded (sender_domain) rather than required to match.
"""

import email
import email.utils
import re
from dataclasses import dataclass

# bestguesspass: Microsoft's verdict when the sender publishes no DMARC
# record but SPF/DKIM would have passed with alignment.
_PASSING = {"pass", "bestguesspass"}
_DMARC_RE = re.compile(r"\bdmarc=([a-z]+)", re.IGNORECASE)
_HEADER_FROM_RE = re.compile(r"\bheader\.from=([^\s;]+)", re.IGNORECASE)


@dataclass(frozen=True)
class SenderAuth:
    # None = no verdict available (no header, or no dmarc= in it).
    verified: bool | None
    domain: str | None


def authenticate_report_sender(raw_mime: bytes) -> SenderAuth:
    msg = email.message_from_bytes(raw_mime)
    results = msg.get_all("Authentication-Results") or []
    first = str(results[0]) if results else ""

    header_from = _HEADER_FROM_RE.search(first)
    if header_from:
        domain = header_from.group(1).strip().lower().rstrip(".")
    else:
        address = email.utils.parseaddr(msg.get("From", ""))[1]
        domain = address.rpartition("@")[2].lower() or None

    dmarc = _DMARC_RE.search(first)
    if dmarc is None:
        return SenderAuth(verified=None, domain=domain)
    return SenderAuth(verified=dmarc.group(1).lower() in _PASSING, domain=domain)
