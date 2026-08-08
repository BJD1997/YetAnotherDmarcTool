"""MTA-STS (RFC 8461) best-practice checker: DNS TXT record presence/format,
HTTPS policy fetch from the well-known path, policy syntax, and cross-check
against the domain's actual MX records — an enforce-mode policy that doesn't
cover a real MX host causes legitimate mail to be rejected by any sender
that honors it, which is worse than not having MTA-STS at all."""

import dataclasses
import re

import httpx

from app.services.dns_checks.base import Finding, is_null_mx
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx, resolve_txt_strict

_TXT_RE = re.compile(r"(?i)^v=STSv1;\s*id=([A-Za-z0-9]+);?\s*$")
POLICY_FETCH_TIMEOUT = 10.0


@dataclasses.dataclass
class PolicyFetchResult:
    body: str | None
    error: str | None


async def fetch_policy_file(domain: str) -> PolicyFetchResult:
    """GETs the well-known MTA-STS policy file. `error` is a human-readable
    string on any failure (connection error, non-200, ...); `body` is the
    raw text on success. Shared by check() (best-practice linting) and the
    policy builder (GET /domains/{id}/dns/mta-sts-builder, showing the
    user their current policy to edit) — one implementation of the fetch,
    not two."""
    policy_url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
    try:
        async with httpx.AsyncClient(timeout=POLICY_FETCH_TIMEOUT) as client:
            response = await client.get(policy_url)
    except httpx.RequestError as exc:
        # httpx's own exceptions (ConnectTimeout, ConnectError, ...) often
        # have an empty str() — the type name carries the actual meaning.
        error_detail = str(exc) or type(exc).__name__
        return PolicyFetchResult(body=None, error=f"Could not fetch MTA-STS policy from {policy_url}: {error_detail}")
    if response.status_code != 200:
        return PolicyFetchResult(body=None, error=f"MTA-STS policy fetch returned HTTP {response.status_code}")
    return PolicyFetchResult(body=response.text, error=None)


def _parse_policy(body: str) -> dict[str, str | list[str]]:
    fields: dict[str, str | list[str]] = {}
    mx_patterns: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "mx":
            mx_patterns.append(value)
        else:
            fields[key] = value
    if mx_patterns:
        fields["mx"] = mx_patterns
    return fields


def _mx_covered(mx_host: str, patterns: list[str]) -> bool:
    """RFC 8461 §4.1: a leading '*.' wildcard matches exactly one label, not
    'one or more' — *.mx.microsoft covers foo.mx.microsoft but NOT
    foo.bar.mx.microsoft. Confirmed live against a real Microsoft 365
    customer domain during development: its real MX host (two labels ahead
    of mx.microsoft) is NOT covered by mx: *.mx.microsoft, and Google's own
    TLS-RPT reports show it correctly rejecting the connection over exactly
    this mismatch — the previous (mx_host.count(".") > suffix.count("."))
    check accepted any extra depth and would have silently passed this."""
    mx_host = mx_host.rstrip(".").lower()
    for pattern in patterns:
        pattern = pattern.rstrip(".").lower()
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if mx_host.endswith("." + suffix):
                remainder = mx_host[: -(len(suffix) + 1)]
                if remainder and "." not in remainder:
                    return True
        elif mx_host == pattern:
            return True
    return False


async def check(domain: str) -> list[Finding]:
    try:
        mx_records = await resolve_mx(domain)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"Could not look up MX records for MTA-STS check: {exc}")]

    if not mx_records or is_null_mx(mx_records):
        # No mail is received here — MTA-STS (inbound transport security)
        # has nothing to protect, same reasoning starttls.py/dane.py
        # already apply.
        return []

    txt_name = f"_mta-sts.{domain}"
    try:
        txt_records = await resolve_txt_strict(txt_name)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"MTA-STS TXT lookup failed: {exc}")]

    sts_records = [r for r in txt_records if r.strip().lower().startswith("v=stsv1")]
    if not sts_records:
        return [
            Finding(
                status="warn",
                summary="No MTA-STS record found",
                details={
                    "recommendation": (
                        f'Publish a TXT record at {txt_name} (e.g. "v=STSv1; id=<unique-string>") and a policy '
                        f"file at https://mta-sts.{domain}/.well-known/mta-sts.txt to require TLS for inbound mail."
                    )
                },
            )
        ]

    findings: list[Finding] = []
    if len(sts_records) > 1:
        findings.append(
            Finding(status="fail", summary=f"{len(sts_records)} MTA-STS TXT records found at {txt_name} (must be exactly one)")
        )

    match = _TXT_RE.match(sts_records[0].strip())
    if not match:
        findings.append(Finding(status="fail", summary=f"Malformed MTA-STS TXT record: {sts_records[0]!r}"))
        return findings

    findings.append(Finding(status="pass", summary=f"MTA-STS TXT record found (id={match.group(1)})"))

    fetch_result = await fetch_policy_file(domain)
    if fetch_result.error is not None:
        findings.append(Finding(status="fail", summary=fetch_result.error))
        return findings

    policy = _parse_policy(fetch_result.body or "")
    version = policy.get("version")
    if version != "STSv1":
        findings.append(Finding(status="fail", summary=f"MTA-STS policy has invalid/missing version: {version!r}"))
        return findings

    mode = policy.get("mode")
    if mode == "enforce":
        findings.append(Finding(status="pass", summary="MTA-STS policy mode is 'enforce'"))
    elif mode == "testing":
        findings.append(
            Finding(
                status="warn",
                summary="MTA-STS policy mode is 'testing' — move to 'enforce' once confident",
                details={"recommendation": "Change mode to 'enforce' in the published policy once you've confirmed it doesn't block legitimate mail."},
            )
        )
    elif mode == "none":
        findings.append(
            Finding(
                status="warn",
                summary="MTA-STS policy mode is 'none' — TLS is not being required",
                details={"recommendation": "Change mode to 'testing' or 'enforce' to actually require TLS for inbound mail."},
            )
        )
    else:
        findings.append(Finding(status="fail", summary=f"MTA-STS policy has invalid/missing mode: {mode!r}"))

    mx_patterns = policy.get("mx", [])
    if not isinstance(mx_patterns, list) or not mx_patterns:
        findings.append(Finding(status="fail", summary="MTA-STS policy has no mx: entries"))
        return findings

    findings.append(
        Finding(status="pass", summary=f"MTA-STS policy covers {len(mx_patterns)} mx pattern(s)", details={"mx_patterns": mx_patterns})
    )

    if mx_records:
        uncovered = [host for _preference, host in mx_records if not _mx_covered(host, mx_patterns)]
        if uncovered:
            findings.append(
                Finding(
                    status="fail" if mode == "enforce" else "warn",
                    summary=f"MTA-STS policy does not cover actual MX host(s): {', '.join(uncovered)}",
                    details={"uncovered_hosts": uncovered},
                )
            )
        else:
            findings.append(Finding(status="pass", summary="All actual MX hosts are covered by the MTA-STS policy"))

    return findings
