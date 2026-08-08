"""SPF (RFC 7208) best-practice checker: exactly one record, the 10-lookup
limit (recursively resolving include:/redirect= chains — those are the only
mechanisms that pull in a whole other SPF record to keep counting into; a/mx
each cost exactly 1 lookup regardless of how many A/AAAA or MX records they
resolve to), the 2-void-lookup limit, deprecated 'ptr' mechanism usage, and
the terminal 'all' qualifier strength.

Deliberately not a full SPF evaluator (it doesn't evaluate against a
specific sending IP) — this counts/validates structure for a best-practices
lint, which is what actually matters here.
"""

import re

from app.models.enums import DomainMailProfile, SpfAllQualifierMode
from app.services.dns_checks.base import Finding
from app.services.dns_checks.resolver import DnsLookupError, resolve_txt_strict

MAX_LOOKUPS = 10
MAX_VOID_LOOKUPS = 2
_SPF_PREFIX_RE = re.compile(r"(?i)^v=spf1(\s|$)")
_ALL_RE = re.compile(r"(?i)^[+\-~?]?all$")


class _State:
    def __init__(self) -> None:
        self.lookup_count = 0
        self.void_lookup_count = 0
        self.visited: set[str] = set()
        self.notes: list[tuple[str, str]] = []  # (status, message)


async def _spf_records_for(domain: str) -> list[str]:
    txt_records = await resolve_txt_strict(domain)
    return [r for r in txt_records if _SPF_PREFIX_RE.match(r.strip())]


async def _walk(domain: str, state: _State, depth: int) -> list[str] | None:
    """Returns the mechanism tokens of `domain`'s SPF record, or None if it
    has no SPF record at all. Recursively follows include:/redirect= and
    tallies lookup/void-lookup counts as it goes."""
    if depth > 10 or domain in state.visited:
        state.notes.append(("warn", f"possible include/redirect loop involving {domain}"))
        return None
    state.visited.add(domain)

    try:
        records = await _spf_records_for(domain)
    except DnsLookupError:
        state.void_lookup_count += 1
        return None

    if not records:
        if depth > 0:
            state.void_lookup_count += 1
        return None
    if len(records) > 1:
        state.notes.append(("fail", f"{domain} has {len(records)} SPF records (RFC 7208 requires exactly one)"))

    tokens = records[0].split()[1:]
    redirect_target: str | None = None

    for token in tokens:
        body = token[1:] if token and token[0] in "+-~?" else token
        if body.lower().startswith("redirect="):
            redirect_target = body.split("=", 1)[1]
            continue
        mech = re.split(r"[:=]", body, maxsplit=1)[0].lower()
        if mech == "ptr":
            state.lookup_count += 1
            state.notes.append(("warn", f"{domain} uses the deprecated 'ptr' mechanism"))
        elif mech in ("a", "mx", "exists"):
            state.lookup_count += 1
        elif mech == "include":
            state.lookup_count += 1
            include_target = body.split(":", 1)[1] if ":" in body else None
            if include_target:
                await _walk(include_target, state, depth + 1)

    if redirect_target:
        state.lookup_count += 1
        await _walk(redirect_target, state, depth + 1)

    return tokens


async def check(
    domain: str,
    mail_profile: DomainMailProfile = DomainMailProfile.sends_mail,
    dmarc_policy: str | None = None,
    all_qualifier_mode: SpfAllQualifierMode = SpfAllQualifierMode.strict,
) -> list[Finding]:
    state = _State()
    try:
        top_tokens = await _walk(domain, state, 0)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"SPF lookup failed: {exc}")]

    if top_tokens is None:
        return [
            Finding(
                status="fail",
                summary="No SPF record found",
                details={"recommendation": 'Publish a TXT record — e.g. "v=spf1 -all" if this domain sends no mail.'},
            )
        ]

    findings = [Finding(status=status, summary=message) for status, message in state.notes]

    if state.lookup_count > MAX_LOOKUPS:
        findings.append(
            Finding(
                status="fail",
                summary=f"SPF exceeds the RFC 7208 10-lookup limit ({state.lookup_count} lookups)",
                details={"lookup_count": state.lookup_count, "limit": MAX_LOOKUPS},
            )
        )
    elif state.lookup_count >= MAX_LOOKUPS - 2:
        findings.append(
            Finding(
                status="warn",
                summary=f"SPF is close to the RFC 7208 10-lookup limit ({state.lookup_count}/{MAX_LOOKUPS})",
                details={"lookup_count": state.lookup_count, "limit": MAX_LOOKUPS},
            )
        )
    else:
        findings.append(
            Finding(
                status="pass",
                summary=f"SPF lookup count OK ({state.lookup_count}/{MAX_LOOKUPS})",
                details={"lookup_count": state.lookup_count, "limit": MAX_LOOKUPS},
            )
        )

    if state.void_lookup_count > MAX_VOID_LOOKUPS:
        findings.append(
            Finding(
                status="fail",
                summary=f"SPF has {state.void_lookup_count} void lookups (RFC 7208 limit is {MAX_VOID_LOOKUPS})",
                details={"void_lookup_count": state.void_lookup_count, "limit": MAX_VOID_LOOKUPS},
            )
        )

    # conditional mode only prefers ~all once DMARC is actually the thing
    # doing enforcement (p=quarantine/reject) for a domain that sends mail —
    # a sends_mail domain still at p=none, or a domain with no DMARC record
    # at all (dmarc_policy=None either way), keeps the strict-mode default
    # of preferring -all, since nothing else is enforcing yet.
    prefer_softfail = (
        all_qualifier_mode == SpfAllQualifierMode.conditional
        and mail_profile == DomainMailProfile.sends_mail
        and dmarc_policy in ("quarantine", "reject")
    )

    all_qualifier = next((t for t in top_tokens if _ALL_RE.match(t)), None)
    if all_qualifier is None:
        findings.append(
            Finding(
                status="warn",
                summary="No 'all' mechanism at the end of the SPF record (defaults to a weak implicit ?all)",
                details={"recommendation": "Add an explicit -all (or ~all while still building confidence) at the end of the record."},
            )
        )
    elif all_qualifier.startswith("-"):
        if prefer_softfail:
            findings.append(
                Finding(
                    status="warn",
                    summary="SPF ends in -all (hardfail) — with DMARC already enforcing, ~all is recommended instead",
                    details={
                        "recommendation": (
                            "Your DMARC policy already rejects/quarantines unaligned mail, so -all adds no extra "
                            "security — it only risks an SMTP-level bounce on relayed/forwarded mail before DKIM/DMARC "
                            "get evaluated. Switch to ~all."
                        )
                    },
                )
            )
        else:
            findings.append(Finding(status="pass", summary="SPF ends in -all (hardfail)"))
    elif all_qualifier.startswith("~"):
        if prefer_softfail:
            findings.append(
                Finding(status="pass", summary="SPF ends in ~all (softfail) — appropriate since DMARC is already enforcing")
            )
        else:
            findings.append(
                Finding(
                    status="warn",
                    summary="SPF ends in ~all (softfail) — consider -all once you're confident in the record",
                    details={"recommendation": "Once the Outbound email table shows a consistently high pass rate for a few weeks, tighten this to -all."},
                )
            )
    else:
        findings.append(
            Finding(
                status="warn",
                summary=f"SPF ends in a weak '{all_qualifier}' qualifier",
                details={"recommendation": "Use -all (hardfail) or ~all (softfail) instead."},
            )
        )

    return findings
