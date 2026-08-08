"""MX record best-practice checker: presence (or an explicit RFC 7505 null
MX for intentionally non-sending domains), and per-host validation that the
target isn't a CNAME (RFC 2181 forbids this) and actually resolves."""

from app.services.dns_checks.base import Finding, is_null_mx
from app.services.dns_checks.resolver import DnsLookupError, resolve_address, resolve_cname, resolve_mx


async def check(domain: str) -> list[Finding]:
    try:
        mx_records = await resolve_mx(domain)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"MX lookup failed: {exc}")]

    if not mx_records:
        return [
            Finding(
                status="warn",
                summary="No MX records found",
                details={
                    "recommendation": (
                        'If this (sub)domain never sends or receives mail, publish an explicit null MX '
                        '("0 .") per RFC 7505 rather than leaving MX absent — some validators (and some '
                        "SPF/DMARC-adjacent tooling) treat absent MX as unconfigured rather than intentional."
                    )
                },
            )
        ]

    if is_null_mx(mx_records):
        return [Finding(status="pass", summary="Null MX (RFC 7505) — explicitly configured to not receive mail")]

    findings = [
        Finding(
            status="pass",
            summary=f"{len(mx_records)} MX record(s) found",
            details={"records": [{"preference": pref, "exchange": host} for pref, host in mx_records]},
        )
    ]

    for _preference, host in mx_records:
        # Address lookup first, deliberately: it's the check that actually
        # matters for deliverability, and some real-world authoritative
        # servers (observed against Microsoft 365's MX hosts) answer A/AAAA
        # queries fine but SERVFAIL a bare CNAME-type query even for names
        # that plainly aren't CNAMEs. A successful address lookup is the
        # stronger signal, so it's used below to downgrade a flaky CNAME
        # query into "assume no CNAME" instead of a hard error.
        try:
            addresses = await resolve_address(host)
        except DnsLookupError as exc:
            findings.append(
                Finding(status="error", summary=f"Could not resolve addresses for MX target {host}: {exc}", subject=host)
            )
            continue

        try:
            cname_target = await resolve_cname(host)
        except DnsLookupError:
            if not addresses:
                findings.append(Finding(status="error", summary=f"Could not check MX target {host}", subject=host))
                continue
            cname_target = None  # addresses resolved fine — treat the flaky CNAME query as "no CNAME"

        if cname_target is not None:
            findings.append(
                Finding(
                    status="fail",
                    summary=f"MX target {host} is a CNAME (points to {cname_target}) — RFC 2181 prohibits this",
                    subject=host,
                )
            )
        elif not addresses:
            findings.append(Finding(status="fail", summary=f"MX target {host} has no A/AAAA record", subject=host))
        else:
            findings.append(
                Finding(status="pass", summary=f"MX target {host} resolves OK", subject=host, details={"addresses": addresses})
            )

    return findings
