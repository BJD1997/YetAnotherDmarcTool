"""DANE/TLSA for SMTP (RFC 7672, built on RFC 6698) best-practice checker:
per-MX-host TLSA record presence at _25._tcp.<mx-host>, structural validity,
and — critically — that the resolver actually DNSSEC-validated the answer.
RFC 7672 requires DNSSEC validation before a TLSA record can be trusted at
all: an unvalidated TLSA answer isn't "unverified but probably fine", it
means anyone off-path could have spoofed a matching-looking response, so
that case is reported as a failure rather than downgraded to a warning.

Per the plan, DNSSEC status is read from the resolver's AD flag (the
`resolver` container validates DNSSEC itself) rather than hand-rolling
DS/DNSKEY/RRSIG chain validation here."""

from app.services.dns_checks.base import Finding, is_null_mx
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx, resolve_tlsa

_VALID_USAGE = {0, 1, 2, 3}
_VALID_SELECTOR = {0, 1}
# matching type -> expected hex-encoded association-data length (0 = full cert/key, any length)
_MATCHING_TYPE_HEX_LEN = {0: None, 1: 64, 2: 128}


def _validate_record(usage: int, selector: int, mtype: int, cert_hex: str) -> str | None:
    if usage not in _VALID_USAGE:
        return f"invalid certificate usage {usage}"
    if selector not in _VALID_SELECTOR:
        return f"invalid selector {selector}"
    if mtype not in _MATCHING_TYPE_HEX_LEN:
        return f"invalid matching type {mtype}"
    expected_len = _MATCHING_TYPE_HEX_LEN[mtype]
    if expected_len is not None and len(cert_hex) != expected_len:
        return f"association data length {len(cert_hex)} doesn't match matching type {mtype} (expected {expected_len})"
    return None


async def check(domain: str) -> list[Finding]:
    try:
        mx_records = await resolve_mx(domain)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"Could not look up MX records for DANE check: {exc}")]

    if not mx_records or is_null_mx(mx_records):
        # No mail is received here (absent MX, or an explicit RFC 7505 null
        # MX) — DANE has nothing to protect; mx.py already flags MX-level issues.
        return []

    findings: list[Finding] = []
    for _preference, host in mx_records:
        tlsa_name = f"_25._tcp.{host}"
        try:
            records, dnssec_validated = await resolve_tlsa(tlsa_name)
        except DnsLookupError as exc:
            findings.append(Finding(status="error", summary=f"TLSA lookup failed for {host}: {exc}", subject=host))
            continue

        if not records:
            findings.append(
                Finding(
                    status="warn",
                    summary=f"No TLSA record for {host} — DANE not deployed for this MX host",
                    subject=host,
                    details={"recommendation": f"Publish a TLSA record at {tlsa_name} to enable DANE for inbound SMTP TLS."},
                )
            )
            continue

        if not dnssec_validated:
            findings.append(
                Finding(
                    status="fail",
                    summary=(
                        f"TLSA record(s) found for {host} but the resolver did NOT DNSSEC-validate them — this "
                        "domain's (or the MX host's) DNS zone likely isn't properly DNSSEC-signed, so these TLSA "
                        "records can't be trusted (RFC 7672 requires DNSSEC validation)"
                    ),
                    subject=host,
                )
            )
            continue

        bad_records = [
            f"{usage} {selector} {mtype}: {error}"
            for usage, selector, mtype, cert_hex in records
            if (error := _validate_record(usage, selector, mtype, cert_hex))
        ]

        if bad_records:
            findings.append(
                Finding(
                    status="fail",
                    summary=f"{len(bad_records)} malformed TLSA record(s) for {host}",
                    subject=host,
                    details={"errors": bad_records},
                )
            )
        else:
            findings.append(
                Finding(
                    status="pass",
                    summary=f"{len(records)} valid, DNSSEC-validated TLSA record(s) for {host}",
                    subject=host,
                    details={"records": [{"usage": u, "selector": s, "matching_type": m} for u, s, m, _ in records]},
                )
            )

    return findings
