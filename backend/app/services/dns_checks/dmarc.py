"""DMARC (RFC 7489) best-practice checker: presence/uniqueness, policy
strength, pct rollout, rua/ruf presence, the RFC 7489 §7.1 external
destination verification for report addresses on a different domain, and
— given an optional destination — whether rua=/ruf= actually include
this org's own configured mailbox (warn if not; ruf= only checked when
it's configured at all, since it's optional and its absence alone isn't
flagged). Same comparison the DMARC Policy Builder already surfaces for
rua=, mirrored here as a scored finding too — see tls_rpt_check.py's
module docstring for the equivalent reasoning on the TLS-RPT side.

DMARCbis (RFC 9989, published May 2026 — np=/psd=/t= tags, pct= removal)
is deliberately NOT checked here; it gets its own module (dmarcbis.py) that
only covers what's new/changed, rather than being folded into this checker.
"""

import re

from app.services.dns_checks.base import Finding
from app.services.dns_checks.dmarc_record import (
    DMARC_PREFIX_RE,
    fetch_inherited_policy,
    parse_dmarc_tags,
    parse_mailto_targets,
)
from app.services.dns_checks.resolver import DnsLookupError, resolve_txt_strict
from app.services.report_destination import ReportDestination


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
            details={
                "recommendation": (
                    f'On {target_domain}\'s DNS (not {record_domain}\'s) — publish a TXT record at {auth_name} '
                    f'with the value "v=DMARC1". This authorizes {target_domain} to receive DMARC reports on '
                    f"{record_domain}'s behalf."
                )
            },
        )
    return None


async def _check_inherited_policy(domain: str, name: str) -> list[Finding]:
    """No _dmarc.<domain> record of its own — that's not automatically a
    gap: receivers walk up the tree (RFC 9989 §4.10) and apply the first
    record's sp= (or p= if sp= is unset), whether or not that domain is in
    this organization. Only actually unprotected if nothing up the tree has
    a usable policy either. Only the enforcement-policy question is
    inherited here — rua=/pct=/alignment are about the domain's own
    reporting setup, which a domain with no record of its own simply
    doesn't have, so those aren't checked against the parent."""
    try:
        inherited = await fetch_inherited_policy(domain)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"No DMARC record at {name}, and couldn't check the parent domains' policy: {exc}")]

    if inherited is None:
        return [
            Finding(
                status="fail",
                # Exact text: routers/domains.py matches on it.
                summary="No DMARC record found",
                details={
                    "recommendation": f'Publish a TXT record at {name}, e.g. "v=DMARC1; p=none; rua=mailto:...". '
                    "No parent domain has one either, so nothing protects this domain."
                },
            )
        ]

    parent_domain = inherited.from_domain
    source_tag = f"{inherited.tag}="
    policy = inherited.policy

    if policy == "reject":
        return [Finding(status="pass", summary=f"No record of its own — inherits {source_tag}reject from {parent_domain}")]
    if policy == "quarantine":
        return [
            Finding(
                status="warn",
                summary=f"No record of its own — inherits {source_tag}quarantine from {parent_domain}",
                details={
                    "recommendation": f"Consider moving {parent_domain}'s {source_tag} to reject once confident, "
                    f"or publish a stronger explicit policy at {name}."
                },
            )
        ]
    if policy == "none":
        return [
            Finding(
                status="warn",
                summary=f"No record of its own — inherits {source_tag}none from {parent_domain} (monitoring only, no enforcement)",
                details={
                    "recommendation": f"Use the policy builder on {parent_domain} (or publish an explicit record "
                    f"at {name}) to move toward enforcement."
                },
            )
        ]
    return [
        Finding(
            status="fail",
            summary=f"No record of its own, and {parent_domain}'s {source_tag} tag is missing or invalid",
        )
    ]


async def check(domain: str, destination: ReportDestination | None = None) -> list[Finding]:
    name = f"_dmarc.{domain}"
    try:
        records = await resolve_txt_strict(name)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"DMARC lookup failed: {exc}")]

    dmarc_records = [r for r in records if DMARC_PREFIX_RE.match(r.strip())]
    if not dmarc_records:
        return await _check_inherited_policy(domain, name)

    findings: list[Finding] = []
    if len(dmarc_records) > 1:
        findings.append(Finding(status="fail", summary=f"{len(dmarc_records)} DMARC records found at {name} (must be exactly one)"))

    tags = parse_dmarc_tags(dmarc_records[0])
    p = tags.get("p", "").lower()

    if p == "reject":
        findings.append(Finding(status="pass", summary="Policy is p=reject"))
    elif p == "quarantine":
        findings.append(
            Finding(
                status="warn",
                summary="Policy is p=quarantine — consider moving to p=reject once confident",
                details={"recommendation": "Use the policy builder to move to p=reject once your pass rate has been stable for a while."},
            )
        )
    elif p == "none":
        findings.append(
            Finding(
                status="warn",
                summary="Policy is p=none (monitoring only, no enforcement)",
                details={"recommendation": "Use the policy builder to move toward enforcement once senders are reviewed and aligned."},
            )
        )
    else:
        findings.append(Finding(status="fail", summary=f"Missing or invalid p= tag: {tags.get('p')!r}"))

    pct = tags.get("pct")
    if pct is not None and pct != "100":
        findings.append(
            Finding(status="warn", summary=f"pct={pct} — policy only applied to {pct}% of messages", details={"pct": pct})
        )

    if tags.get("sp"):
        findings.append(Finding(status="pass", summary=f"Explicit subdomain policy sp={tags['sp']}", details={"sp": tags["sp"]}))

    rua_targets = parse_mailto_targets(tags.get("rua", ""))
    if not rua_targets:
        findings.append(
            Finding(
                status="warn",
                summary="No rua= (aggregate reporting) address configured — no visibility into DMARC results",
                details={"recommendation": "Use the policy builder to add rua= pointing at your report address."},
            )
        )
    else:
        findings.append(Finding(status="pass", summary=f"Aggregate reports (rua) configured: {', '.join(rua_targets)}"))
        for target in rua_targets:
            ext_finding = await _check_external_destination(domain, target, "rua")
            if ext_finding:
                findings.append(ext_finding)
        # Presence + external authorization don't prove reports actually
        # reach THIS org's own mailbox — same gap tls_rpt_check.py's check()
        # flags directly rather than leaving it to the policy builder alone
        # (see that module's docstring). ruf= gets the identical check below.
        if destination and not destination.reached_by(rua_targets):
            findings.append(
                Finding(
                    status="warn",
                    summary=f"rua= doesn't include your report address ({destination.address}) — aggregate reports won't reach it",
                    details={"recommendation": "Use the policy builder to update rua= to include your report address."},
                )
            )

    ruf_targets = parse_mailto_targets(tags.get("ruf", ""))
    for target in ruf_targets:
        ext_finding = await _check_external_destination(domain, target, "ruf")
        if ext_finding:
            findings.append(ext_finding)
    # ruf= is optional (unlike rua=, its absence isn't flagged at all) —
    # only worth comparing against the org's mailbox when it's actually
    # configured to begin with.
    if ruf_targets and destination and not destination.reached_by(ruf_targets):
        findings.append(
            Finding(
                status="warn",
                summary=f"ruf= doesn't include your report address ({destination.address}) — forensic reports won't reach it",
                details={"recommendation": "Use the policy builder to update ruf= to include your report address."},
            )
        )

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
