"""MTA-STS (RFC 8461) best-practice checker: DNS TXT record presence/format,
HTTPS policy fetch from the well-known path, policy syntax, and cross-check
against the domain's actual MX records — an enforce-mode policy that doesn't
cover a real MX host causes legitimate mail to be rejected by any sender
that honors it, which is worse than not having MTA-STS at all."""

import re

import httpx

from app.services.dns_checks.base import Finding
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx, resolve_txt_strict

_TXT_RE = re.compile(r"(?i)^v=STSv1;\s*id=([A-Za-z0-9]+);?\s*$")
POLICY_FETCH_TIMEOUT = 10.0


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
    mx_host = mx_host.rstrip(".").lower()
    for pattern in patterns:
        pattern = pattern.rstrip(".").lower()
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if mx_host.endswith(suffix) and mx_host.count(".") > suffix.count("."):
                return True
        elif mx_host == pattern:
            return True
    return False


async def check(domain: str) -> list[Finding]:
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

    policy_url = f"https://mta-sts.{domain}/.well-known/mta-sts.txt"
    try:
        async with httpx.AsyncClient(timeout=POLICY_FETCH_TIMEOUT) as client:
            response = await client.get(policy_url)
    except httpx.RequestError as exc:
        # httpx's own exceptions (ConnectTimeout, ConnectError, ...) often
        # have an empty str() — the type name carries the actual meaning.
        error_detail = str(exc) or type(exc).__name__
        findings.append(Finding(status="fail", summary=f"Could not fetch MTA-STS policy from {policy_url}: {error_detail}"))
        return findings

    if response.status_code != 200:
        findings.append(Finding(status="fail", summary=f"MTA-STS policy fetch returned HTTP {response.status_code}"))
        return findings

    policy = _parse_policy(response.text)
    version = policy.get("version")
    if version != "STSv1":
        findings.append(Finding(status="fail", summary=f"MTA-STS policy has invalid/missing version: {version!r}"))
        return findings

    mode = policy.get("mode")
    if mode == "enforce":
        findings.append(Finding(status="pass", summary="MTA-STS policy mode is 'enforce'"))
    elif mode == "testing":
        findings.append(Finding(status="warn", summary="MTA-STS policy mode is 'testing' — move to 'enforce' once confident"))
    elif mode == "none":
        findings.append(Finding(status="warn", summary="MTA-STS policy mode is 'none' — TLS is not being required"))
    else:
        findings.append(Finding(status="fail", summary=f"MTA-STS policy has invalid/missing mode: {mode!r}"))

    mx_patterns = policy.get("mx", [])
    if not isinstance(mx_patterns, list) or not mx_patterns:
        findings.append(Finding(status="fail", summary="MTA-STS policy has no mx: entries"))
        return findings

    findings.append(
        Finding(status="pass", summary=f"MTA-STS policy covers {len(mx_patterns)} mx pattern(s)", details={"mx_patterns": mx_patterns})
    )

    try:
        actual_mx = await resolve_mx(domain)
    except DnsLookupError as exc:
        findings.append(Finding(status="error", summary=f"Could not cross-check MTA-STS policy against MX records: {exc}"))
        return findings

    if actual_mx:
        uncovered = [host for _preference, host in actual_mx if not _mx_covered(host, mx_patterns)]
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
