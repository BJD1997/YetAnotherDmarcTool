"""TLS-RPT (RFC 8460) presence checker: TXT record at _smtp._tls.<domain>
advertising where other mail servers should send SMTP TLS negotiation
failure reports. This is a DNS-publication check only — actually parsing
inbound TLS-RPT reports that arrive in the mailbox is a separate concern
handled by report_writer.write_smtp_tls_report.

Also home to fetch_current_tls_rpt_record/check_tls_rpt_rua_destination,
used by the TLS-RPT policy builder (app/routers/dns_checks.py) — mirrors
dmarc_record.py's fetch_current_dmarc_record/check_rua_destination for the
exact same reason: a domain can publish a syntactically valid record (the
check() below passes it) while rua= points somewhere other than this org's
configured mailbox, a gap invisible to check() by design (same scope
dmarc.py's own rua= check has — presence/validity, not "is it MY mailbox").
"""

import dataclasses
import re

from app.services.dns_checks.base import Finding, is_null_mx
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx, resolve_txt_strict

_TLSRPT_PREFIX_RE = re.compile(r"(?i)^v=TLSRPTv1(;|\s|$)")


def _parse_tags(record: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in record.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        tags[key.strip().lower()] = value.strip()
    return tags


def _parse_rua_uris(value: str) -> list[str]:
    return [uri.strip() for uri in value.split(",") if uri.strip()]


def _parse_mailto_targets(value: str) -> list[str]:
    return [uri[len("mailto:"):] for uri in _parse_rua_uris(value) if uri.lower().startswith("mailto:")]


@dataclasses.dataclass
class TlsRptRecordInfo:
    raw: str
    tags: dict[str, str]


async def fetch_current_tls_rpt_record(domain: str) -> TlsRptRecordInfo | None:
    """Live-fetches _smtp._tls.<domain> and returns the first valid
    v=TLSRPTv1 record (parsed), or None if genuinely absent. Raises
    DnsLookupError — not swallowed — on a real lookup failure, same
    contract as fetch_current_dmarc_record."""
    name = f"_smtp._tls.{domain}"
    records = await resolve_txt_strict(name)
    tlsrpt_records = [r for r in records if _TLSRPT_PREFIX_RE.match(r.strip())]
    if not tlsrpt_records:
        return None
    return TlsRptRecordInfo(raw=tlsrpt_records[0], tags=_parse_tags(tlsrpt_records[0]))


@dataclasses.dataclass
class TlsRptRuaDestinationCheck:
    status: str  # "not_configured" | "lookup_error" | "no_rua" | "points_elsewhere" | "correct"
    current_targets: list[str]


async def check_tls_rpt_rua_destination(domain: str, mailbox_address: str) -> TlsRptRuaDestinationCheck:
    """Does this domain's published TLS-RPT rua= actually include the org's
    report destination? Same question dmarc_record.check_rua_destination
    answers for DMARC, applied to _smtp._tls.<domain> instead."""
    try:
        record = await fetch_current_tls_rpt_record(domain)
    except DnsLookupError:
        return TlsRptRuaDestinationCheck(status="lookup_error", current_targets=[])
    if record is None:
        return TlsRptRuaDestinationCheck(status="not_configured", current_targets=[])
    targets = _parse_mailto_targets(record.tags.get("rua", ""))
    if not targets:
        return TlsRptRuaDestinationCheck(status="no_rua", current_targets=[])
    if mailbox_address.lower() in (t.lower() for t in targets):
        return TlsRptRuaDestinationCheck(status="correct", current_targets=targets)
    return TlsRptRuaDestinationCheck(status="points_elsewhere", current_targets=targets)


async def check(domain: str) -> list[Finding]:
    try:
        mx_records = await resolve_mx(domain)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"Could not look up MX records for TLS-RPT check: {exc}")]

    if not mx_records or is_null_mx(mx_records):
        # No mail is received here — nothing for TLS-RPT to ever report a
        # negotiation failure on, same reasoning starttls.py/dane.py/
        # mta_sts.py already apply.
        return []

    name = f"_smtp._tls.{domain}"
    try:
        records = await resolve_txt_strict(name)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"TLS-RPT lookup failed: {exc}")]

    tlsrpt_records = [r for r in records if _TLSRPT_PREFIX_RE.match(r.strip())]
    if not tlsrpt_records:
        return [
            Finding(
                status="warn",
                summary="No TLS-RPT record found",
                details={
                    "recommendation": (
                        f'Publish a TXT record at {name}, e.g. "v=TLSRPTv1; rua=mailto:tls-reports@yourdomain" '
                        "to receive reports when other mail servers fail to negotiate TLS to you."
                    )
                },
            )
        ]

    findings: list[Finding] = []
    if len(tlsrpt_records) > 1:
        findings.append(
            Finding(status="fail", summary=f"{len(tlsrpt_records)} TLS-RPT records found at {name} (must be exactly one)")
        )

    tags = _parse_tags(tlsrpt_records[0])
    rua_uris = _parse_rua_uris(tags.get("rua", ""))

    if not rua_uris:
        findings.append(Finding(status="fail", summary="TLS-RPT record has no rua= reporting destination"))
        return findings

    invalid = [u for u in rua_uris if not (u.lower().startswith("mailto:") or u.lower().startswith("https:"))]
    valid = [u for u in rua_uris if u not in invalid]

    if invalid:
        findings.append(
            Finding(
                status="fail",
                summary=f"TLS-RPT rua= has unsupported URI scheme(s): {', '.join(invalid)} (only mailto: and https: are valid)",
            )
        )
    if valid:
        findings.append(Finding(status="pass", summary=f"TLS-RPT reports configured: {', '.join(valid)}"))

    return findings
