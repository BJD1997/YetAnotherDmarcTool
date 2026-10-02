"""Was a report email really sent by who it says? Anyone can mail a DMARC
reporting address, so a report's own claims (org_name, report_id) prove
nothing. The evidence is the receiving mailbox's own verdict on the email,
from headers Exchange Online stamps on inbound mail. Three facts are recorded
per report:

- origin: "external" when the email came from outside the tenant
  (X-MS-Exchange-Organization-AuthAs: Anonymous — Exchange strips
  X-MS-Exchange-Organization-* headers from external mail, so this can't be
  forged from outside), otherwise "internal". Internal mail never gets EOP's
  verdict, so any Authentication-Results on it could be self-written.
- dmarc: the DMARC result in the FIRST Authentication-Results header (the
  receiving server prepends its own; one a forger wrote sits below it), or
  "missing".
- matches_reporter: whether the authenticated From domain belongs to the
  reporter the report names (reporter_matches); None when the report names
  none (forensic reports).

decide() turns those into counted / left out under the organization's
report_sender_check setting; decide_sql() is the same rule for re-evaluating
stored reports when the setting changes.
"""

import email
import email.utils
import re
from dataclasses import dataclass, replace

from parsedmarc.utils import get_base_domain
from sqlalchemy import and_, case, false, null, true

from app.models.enums import ReportSenderCheck

# bestguesspass: Microsoft's verdict when the sender publishes no DMARC
# record but SPF/DKIM would have passed with alignment.
_PASSING = {"pass", "bestguesspass"}
_DMARC_RE = re.compile(r"\bdmarc=([a-z]+)", re.IGNORECASE)
_HEADER_FROM_RE = re.compile(r"\bheader\.from=([^\s;]+)", re.IGNORECASE)


# Large reporters that legitimately send from a different organizational
# domain than the one in their report's contact address:
# sender's organizational domain -> organizational domains it may report for.
_REPORTER_ALIASES = {
    "yahoo.com": {"yahooinc.com"},  # From dmarc.yahoo.com, contact dmarchelp@yahooinc.com
}


@dataclass(frozen=True)
class SenderAuth:
    # All None: not checked (no email at hand). Otherwise origin is set.
    origin: str | None
    dmarc: str | None
    domain: str | None
    matches_reporter: bool | None = None

    def for_reporter(self, claimed: list[str]) -> "SenderAuth":
        """Records whether the sender is the reporter the report names."""
        if self.origin is None or not claimed:
            return self
        return replace(self, matches_reporter=reporter_matches(self.domain, claimed))


UNCHECKED = SenderAuth(origin=None, dmarc=None, domain=None)


def decide(mode: ReportSenderCheck, sender: SenderAuth) -> bool | None:
    """Counted (True), left out (False), or not checked (None)."""
    if sender.origin is None:
        return None
    if mode == ReportSenderCheck.off:
        return True
    external = sender.origin == "external"
    if mode == ReportSenderCheck.strict:
        return external and sender.dmarc in _PASSING and sender.matches_reporter is not False
    return external and sender.dmarc != "fail" and sender.matches_reporter is not False


def left_out_reason(sender: SenderAuth) -> str:
    """Why a report was left out, in the words the review list shows."""
    who = sender.domain or "an unknown sender"
    if sender.origin != "external":
        return "Sent from inside your Microsoft 365 organization"
    if sender.matches_reporter is False:
        return f"Sent by {who}, not by the reporter it names"
    if sender.dmarc == "fail":
        return f"Failed DMARC for {who}"
    return f"Didn't pass DMARC for {who} (result: {sender.dmarc})"


def decide_sql(mode: ReportSenderCheck, model):
    """decide() as a SQL expression over a report table's sender_* columns."""
    if mode == ReportSenderCheck.off:
        counted = true()
    else:
        external = model.sender_origin == "external"
        matches = model.sender_matches_reporter.is_not(False)
        if mode == ReportSenderCheck.strict:
            counted = and_(external, model.sender_dmarc.in_(sorted(_PASSING)), matches)
        else:
            counted = and_(external, model.sender_dmarc.is_distinct_from("fail"), matches)
    return case((model.sender_origin.is_(None), null()), (counted, true()), else_=false())


def _organizational(domain: str) -> str:
    domain = domain.strip().lower().rstrip(".")
    return get_base_domain(domain) or domain


def reporter_matches(sender_domain: str | None, claimed: list[str]) -> bool:
    if not sender_domain:
        return False
    sender = _organizational(sender_domain)
    allowed = {sender} | _REPORTER_ALIASES.get(sender, set())
    return any(_organizational(c) in allowed for c in claimed if c)


def claimed_domain(value: str | None) -> str | None:
    """A domain from a report's contact field: an email address, a mailto:
    or https:// URI, or a bare domain name. None if it's none of those
    (e.g. an organization name like "Google Inc.")."""
    if not value:
        return None
    value = value.strip().lower()
    if value.startswith("mailto:"):
        value = value[7:]
    if "@" in value:
        return value.rpartition("@")[2] or None
    if "://" in value:
        value = value.split("://", 1)[1].split("/", 1)[0]
    return value if "." in value and " " not in value else None


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

    # Required, not just "not internal": its absence means the email didn't
    # arrive through Exchange Online's inbound path at all. (A future
    # non-Exchange connector, e.g. IMAP, needs its own trust source here.)
    auth_as = (msg.get("X-MS-Exchange-Organization-AuthAs") or "").strip().lower()
    origin = "external" if auth_as == "anonymous" else "internal"

    dmarc = _DMARC_RE.search(first)
    return SenderAuth(origin=origin, dmarc=dmarc.group(1).lower() if dmarc else "missing", domain=domain)
