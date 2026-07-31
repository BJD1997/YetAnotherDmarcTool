"""DMARC (RFC 7489) best-practice checker: presence/uniqueness, policy
strength, pct rollout, rua/ruf presence, and the RFC 7489 §7.1 external
destination verification for report addresses on a different domain.

DMARCbis (RFC 9989, published May 2026 — np=/psd=/t= tags, pct= removal)
is deliberately NOT checked here; it gets its own module (dmarcbis.py) that
only covers what's new/changed, rather than being folded into this checker.
"""

import re

from app.services.dns_checks.base import Finding
from app.services.dns_checks.resolver import DnsLookupError, resolve_txt_strict

_DMARC_PREFIX_RE = re.compile(r"(?i)^v=DMARC1(;|\s|$)")


def _parse_tags(record: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in record.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        tags[key.strip().lower()] = value.strip()
    return tags


def _parse_mailto_targets(value: str) -> list[str]:
    targets = []
    for uri in value.split(","):
        uri = uri.strip()
        if uri.lower().startswith("mailto:"):
            # strip an optional "!10m" size-limit suffix
            targets.append(uri[len("mailto:"):].split("!")[0])
    return targets


async def _check_external_destination(record_domain: str, report_email: str, tag_name: str) -> Finding | None:
    if "@" not in report_email:
        return None
    target_domain = report_email.rsplit("@", 1)[1].rstrip(".")

    # Same domain, or reporting up to a parent domain, doesn't need external
    # authorization. (Simplified vs. RFC 7489's exact "organizational
    # domain" equality test — sibling subdomains of the same org domain
    # would be flagged here even though technically exempt; erring toward
    # over-flagging is the safer direction for a best-practices linter.)
    if target_domain.lower() == record_domain.lower() or record_domain.lower().endswith(f".{target_domain.lower()}"):
        return None

    auth_name = f"{record_domain}._report._dmarc.{target_domain}"
    try:
        auth_records = await resolve_txt_strict(auth_name)
    except DnsLookupError as exc:
        return Finding(status="error", summary=f"Could not verify external {tag_name} destination {report_email}: {exc}")

    if not any(re.match(r"(?i)^v=DMARC1", r.strip()) for r in auth_records):
        return Finding(
            status="fail",
            summary=(
                f"{tag_name}= points to {report_email} on a different domain, but {auth_name} doesn't authorize "
                f"it — most receivers will refuse to send reports there (RFC 7489 §7.1)"
            ),
        )
    return None


async def check(domain: str) -> list[Finding]:
    name = f"_dmarc.{domain}"
    try:
        records = await resolve_txt_strict(name)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"DMARC lookup failed: {exc}")]

    dmarc_records = [r for r in records if _DMARC_PREFIX_RE.match(r.strip())]
    if not dmarc_records:
        return [
            Finding(
                status="fail",
                summary="No DMARC record found",
                details={"recommendation": f'Publish a TXT record at {name}, e.g. "v=DMARC1; p=none; rua=mailto:..."'},
            )
        ]

    findings: list[Finding] = []
    if len(dmarc_records) > 1:
        findings.append(Finding(status="fail", summary=f"{len(dmarc_records)} DMARC records found at {name} (must be exactly one)"))

    tags = _parse_tags(dmarc_records[0])
    p = tags.get("p", "").lower()

    if p == "reject":
        findings.append(Finding(status="pass", summary="Policy is p=reject"))
    elif p == "quarantine":
        findings.append(Finding(status="warn", summary="Policy is p=quarantine — consider moving to p=reject once confident"))
    elif p == "none":
        findings.append(Finding(status="warn", summary="Policy is p=none (monitoring only, no enforcement)"))
    else:
        findings.append(Finding(status="fail", summary=f"Missing or invalid p= tag: {tags.get('p')!r}"))

    pct = tags.get("pct")
    if pct is not None and pct != "100":
        findings.append(
            Finding(status="warn", summary=f"pct={pct} — policy only applied to {pct}% of messages", details={"pct": pct})
        )

    if tags.get("sp"):
        findings.append(Finding(status="pass", summary=f"Explicit subdomain policy sp={tags['sp']}", details={"sp": tags["sp"]}))

    rua_targets = _parse_mailto_targets(tags.get("rua", ""))
    if not rua_targets:
        findings.append(
            Finding(status="warn", summary="No rua= (aggregate reporting) address configured — no visibility into DMARC results")
        )
    else:
        findings.append(Finding(status="pass", summary=f"Aggregate reports (rua) configured: {', '.join(rua_targets)}"))
        for target in rua_targets:
            ext_finding = await _check_external_destination(domain, target, "rua")
            if ext_finding:
                findings.append(ext_finding)

    for target in _parse_mailto_targets(tags.get("ruf", "")):
        ext_finding = await _check_external_destination(domain, target, "ruf")
        if ext_finding:
            findings.append(ext_finding)

    adkim = tags.get("adkim", "r")
    aspf = tags.get("aspf", "r")
    findings.append(
        Finding(
            status="pass",
            summary=f"Alignment: DKIM={'strict' if adkim == 's' else 'relaxed'}, SPF={'strict' if aspf == 's' else 'relaxed'}",
            details={"adkim": adkim, "aspf": aspf},
        )
    )

    return findings
