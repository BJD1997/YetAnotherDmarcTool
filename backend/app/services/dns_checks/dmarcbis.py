"""DMARCbis (RFC 9989 — published May 2026, obsoleting RFC 7489 and
promoting DMARC to full Standards Track; aggregate/failure reporting moved
out to companion RFCs 9990/9991) checker.

Covers ONLY what changed vs RFC 7489 — the structural checks that didn't
change (p=/sp=/rua=/ruf=/adkim=/aspf=, and the _report._dmarc external
destination verification mechanism, confirmed unchanged in RFC 9990 §4)
already live in dmarc.py and aren't duplicated here:

- pct= is REMOVED entirely (RFC 9989 Appendix A.6) — publishing it is now
  non-standard. Flagged as a deprecation warning, not a hard failure, since
  RFC-7489-era receivers still honor it during the ecosystem transition.
- np= (non-existent-subdomain policy) is NEW — same none/quarantine/reject
  syntax as p=/sp=. Silently defaults to sp= (or p= if sp= is also absent)
  when unset, so — mirroring how dmarc.py treats the equally-optional sp= —
  absence isn't flagged, only an invalid value if the tag is present.
- psd= (Public Suffix Domain indicator) is NEW — y/n/u, default u. Only
  relevant to domains that ARE public suffixes (registries/registrars);
  flagged only if present with an invalid value.
- t= (test mode) is NEW — y/n, default n. When y, receivers apply policy one
  level weaker than published (reject -> quarantine, quarantine -> none).

RFC 9989 also replaces Public-Suffix-List-based organizational-domain
determination with an up-to-8-query DNS Tree Walk (halting at a record
carrying psd=y or psd=n) — that's a receiver-side alignment algorithm with
no corresponding "is my own record correct" check to perform here, so it
has no finding of its own.
"""

from app.services.dns_checks.base import Finding
from app.services.dns_checks.resolver import DnsLookupError, resolve_txt_strict

_VALID_POLICY = {"none", "quarantine", "reject"}
_VALID_PSD = {"y", "n", "u"}
_VALID_BOOL = {"y", "n"}


def _parse_tags(record: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in record.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        tags[key.strip().lower()] = value.strip()
    return tags


async def check(domain: str) -> list[Finding]:
    name = f"_dmarc.{domain}"
    try:
        records = await resolve_txt_strict(name)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"DMARC lookup failed: {exc}")]

    dmarc_records = [r for r in records if r.strip().lower().startswith("v=dmarc1")]
    if not dmarc_records:
        # dmarc.py already reports "No DMARC record found" as a fail —
        # no need to duplicate that finding here.
        return []

    tags = _parse_tags(dmarc_records[0])
    findings: list[Finding] = []

    if "pct" in tags:
        findings.append(
            Finding(
                status="warn",
                summary=f"pct={tags['pct']} is published, but RFC 9989 (DMARCbis) removes the pct= tag entirely",
                details={
                    "recommendation": "Remove pct= — it has no effect under RFC 9989, and receivers following the "
                    "new standard will ignore it. Keep it only if you still need RFC 7489-era receivers to ramp "
                    "up enforcement gradually."
                },
            )
        )

    if "np" in tags:
        np_value = tags["np"].lower()
        if np_value not in _VALID_POLICY:
            findings.append(
                Finding(status="fail", summary=f"Invalid np= value: {tags['np']!r} (must be none/quarantine/reject)")
            )
        else:
            findings.append(Finding(status="pass", summary=f"np={np_value} (non-existent-subdomain policy) explicitly set"))

    if "psd" in tags:
        psd_value = tags["psd"].lower()
        if psd_value not in _VALID_PSD:
            findings.append(Finding(status="fail", summary=f"Invalid psd= value: {tags['psd']!r} (must be y/n/u)"))
        else:
            findings.append(Finding(status="pass", summary=f"psd={psd_value} explicitly set"))

    if "t" in tags:
        t_value = tags["t"].lower()
        if t_value not in _VALID_BOOL:
            findings.append(Finding(status="fail", summary=f"Invalid t= value: {tags['t']!r} (must be y or n)"))
        elif t_value == "y":
            findings.append(
                Finding(
                    status="warn",
                    summary="t=y (test mode) is set — receivers apply policy one level weaker than published "
                    "(e.g. reject -> quarantine)",
                )
            )

    if not findings:
        findings.append(Finding(status="pass", summary="No RFC 9989 (DMARCbis)-specific tags in use; nothing new to flag"))

    return findings
