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

Those prove who sent a report, not that the sender is a mail receiver that
ever saw the domain's mail: anyone can register a domain, pass DMARC for it
and name it as the reporter. So a report also only counts when its sending
domain is a known reporter — KNOWN_REPORTERS, or one the organization trusts
(TrustedReporter). Both match the exact sending domain, never its
organizational domain: yahoo.com or mail.ru are also free-mailbox domains
anyone can pass DMARC for, their report systems' dmarc.yahoo.com or
corp.mail.ru aren't.

decide() turns those into counted / left out under the organization's
report_sender_check setting; decide_sql() is the same rule for re-evaluating
stored reports when the setting or the trusted reporters change.
"""

import email
import email.utils
import re
from dataclasses import dataclass, replace

from parsedmarc.utils import get_base_domain
from sqlalchemy import and_, case, exists, false, null, or_, select, true

from app.models.enums import ReportSenderCheck
from app.models.trusted_reporter import TrustedReporter

# bestguesspass: Microsoft's verdict when the sender publishes no DMARC
# record but SPF/DKIM would have passed with alignment.
_PASSING = {"pass", "bestguesspass"}
_DMARC_RE = re.compile(r"\bdmarc=([a-z]+)", re.IGNORECASE)
_HEADER_FROM_RE = re.compile(r"\bheader\.from=([^\s;]+)", re.IGNORECASE)


# Sending domains of the large reporters' report systems, counted without
# the organization having to trust them first. Only domains nobody outside
# the provider can send from (no consumer mailboxes); the migration that
# introduced this (0040_trusted_reporters) froze a copy of this set.
KNOWN_REPORTERS = frozenset({"google.com", "microsoft.com", "dmarc.yahoo.com", "corp.mail.ru"})

# Domains anyone can get a mailbox on and pass DMARC for: trusting one would
# let anybody add reports again, so they can never be trusted. Exact domains
# only: a provider's report system on a subdomain (dmarc.zoho.com) can be.
FREE_MAILBOX_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "aol.com", "icloud.com", "me.com", "mac.com", "gmx.com", "gmx.net", "gmx.de", "web.de",
    "mail.com", "mail.ru", "yandex.ru", "yandex.com", "proton.me", "protonmail.com", "zoho.com", "hey.com",
})


def can_be_trusted(domain: str) -> bool:
    return domain.strip().lower().rstrip(".") not in FREE_MAILBOX_DOMAINS

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


def is_known_reporter(sender: SenderAuth, trusted: frozenset[str] = frozenset()) -> bool:
    return sender.domain is not None and (sender.domain in KNOWN_REPORTERS or sender.domain in trusted)


def decide(mode: ReportSenderCheck, sender: SenderAuth, trusted: frozenset[str] = frozenset()) -> bool | None:
    """Counted (True), left out (False), or not checked (None). trusted: the
    organization's TrustedReporter domains."""
    if sender.origin is None:
        return None
    if mode == ReportSenderCheck.off:
        return True
    external = sender.origin == "external"
    known = is_known_reporter(sender, trusted)
    if mode == ReportSenderCheck.strict:
        return external and sender.dmarc in _PASSING and sender.matches_reporter is not False and known
    return external and sender.dmarc != "fail" and sender.matches_reporter is not False and known


def left_out_reason(sender: SenderAuth, trusted: frozenset[str] = frozenset()) -> str:
    """Why a report was left out, in the words the review list shows."""
    who = sender.domain or "an unknown sender"
    if sender.origin == "unrecognized":
        return "Not checked by your IMAP mail server (its Authentication-Results ID didn't match the one in Settings)"
    if sender.origin != "external":
        return "Sent from inside your Microsoft 365 organization"
    if sender.matches_reporter is False:
        return f"Sent by {who}, not by the reporter it names"
    if sender.dmarc == "fail":
        return f"Failed DMARC for {who}"
    if not is_known_reporter(sender, trusted):
        return f"Sent by {who}, a reporter you haven't trusted yet"
    return f"Didn't pass DMARC for {who} (result: {sender.dmarc})"


def decide_sql(mode: ReportSenderCheck, model):
    """decide() as a SQL expression over a report table's sender_* columns,
    with the organization's trusted reporters looked up per row."""
    if mode == ReportSenderCheck.off:
        counted = true()
    else:
        external = model.sender_origin == "external"
        matches = model.sender_matches_reporter.is_not(False)
        known = or_(
            model.sender_domain.in_(sorted(KNOWN_REPORTERS)),
            exists(
                select(TrustedReporter.id).where(
                    TrustedReporter.organization_id == model.organization_id,
                    TrustedReporter.domain == model.sender_domain,
                )
            ),
        )
        if mode == ReportSenderCheck.strict:
            counted = and_(external, model.sender_dmarc.in_(sorted(_PASSING)), matches, known)
        else:
            counted = and_(external, model.sender_dmarc.is_distinct_from("fail"), matches, known)
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


_AUTHSERV_ID_RE = re.compile(r"\s*([^\s;]+)")


def authserv_id(header_value: str) -> str | None:
    """The authserv-id an Authentication-Results header starts with (RFC 8601
    section 2.2): who did the checks, for example mx.google.com."""
    match = _AUTHSERV_ID_RE.match(header_value or "")
    return match.group(1).lower() if match else None


def top_authserv_id(raw_mime: bytes) -> str | None:
    results = email.message_from_bytes(raw_mime).get_all("Authentication-Results") or []
    return authserv_id(str(results[0])) if results else None


def authenticate_report_sender(raw_mime: bytes, *, imap_authserv_id: str | None = None) -> SenderAuth:
    """imap_authserv_id: for a message read over IMAP, the ID of the
    mailbox's own mail server. Only the top Authentication-Results header
    with that ID is believed: a sender can put fake ones in their message,
    but the receiving server adds its own on top (RFC 8601 section 5)."""
    msg = email.message_from_bytes(raw_mime)
    results = msg.get_all("Authentication-Results") or []
    first = str(results[0]) if results else ""

    if imap_authserv_id is not None:
        trusted = bool(imap_authserv_id) and authserv_id(first) == imap_authserv_id.lower()
        if not trusted:
            first = ""

    header_from = _HEADER_FROM_RE.search(first)
    if header_from:
        domain = header_from.group(1).strip().lower().rstrip(".")
    else:
        address = email.utils.parseaddr(msg.get("From", ""))[1]
        domain = address.rpartition("@")[2].lower() or None

    if imap_authserv_id is not None:
        # IMAP: the mailbox's own server vouched for the result, or nobody did.
        origin = "external" if first else "unrecognized"
    else:
        # Required, not just "not internal": its absence means the email
        # didn't arrive through Exchange Online's inbound path at all.
        auth_as = (msg.get("X-MS-Exchange-Organization-AuthAs") or "").strip().lower()
        origin = "external" if auth_as == "anonymous" else "internal"

    dmarc = _DMARC_RE.search(first)
    return SenderAuth(origin=origin, dmarc=dmarc.group(1).lower() if dmarc else "missing", domain=domain)
