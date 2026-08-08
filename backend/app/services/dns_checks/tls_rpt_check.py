"""TLS-RPT (RFC 8460) presence checker: TXT record at _smtp._tls.<domain>
advertising where other mail servers should send SMTP TLS negotiation
failure reports. This is a DNS-publication check only — actually parsing
inbound TLS-RPT reports that arrive in the mailbox is a separate concern
handled by report_writer.write_smtp_tls_report."""

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
