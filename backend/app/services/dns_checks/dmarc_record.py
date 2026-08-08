"""Shared DMARC record parsing + live-fetch helpers, used by dmarc.py's
best-practice checker AND by onboarding/the policy builder (which need to
read + compare a domain's current published record, not just lint it).
Pulled out into its own module so both sides call one implementation."""

import dataclasses
import re

from app.services.dns_checks.resolver import DnsLookupError, resolve_txt_strict

DMARC_PREFIX_RE = re.compile(r"(?i)^v=DMARC1(;|\s|$)")


def parse_dmarc_tags(record: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in record.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        tags[key.strip().lower()] = value.strip()
    return tags


def parse_mailto_targets(value: str) -> list[str]:
    targets = []
    for uri in value.split(","):
        uri = uri.strip()
        if uri.lower().startswith("mailto:"):
            # strip an optional "!10m" size-limit suffix
            targets.append(uri[len("mailto:"):].split("!")[0])
    return targets


@dataclasses.dataclass
class DmarcRecordInfo:
    raw: str
    tags: dict[str, str]


async def fetch_current_dmarc_record(domain: str) -> DmarcRecordInfo | None:
    """Live-fetches _dmarc.<domain> and returns the first valid v=DMARC1
    record (parsed), or None if genuinely absent (NXDOMAIN/NoAnswer, per
    resolve_txt_strict's contract). Raises DnsLookupError — NOT swallowed —
    on a real lookup failure (timeout/SERVFAIL/etc); callers should treat
    that as "couldn't check" distinctly from "confirmed absent"."""
    name = f"_dmarc.{domain}"
    records = await resolve_txt_strict(name)
    dmarc_records = [r for r in records if DMARC_PREFIX_RE.match(r.strip())]
    if not dmarc_records:
        return None
    return DmarcRecordInfo(raw=dmarc_records[0], tags=parse_dmarc_tags(dmarc_records[0]))


async def resolve_effective_policy(domain: str, parent_domain: str | None = None) -> str | None:
    """The enforcement policy actually covering this domain's mail: its own
    p=, or — if it has no DMARC record of its own, the normal case for a
    subdomain (RFC 7489 inheritance) — the parent's sp= (preferred) or p=.
    Returns None if nothing resolvable either way (including on a lookup
    failure; callers here only need "is DMARC actually enforcing yet",
    where a failed lookup and a confirmed-absent record both mean "can't
    credit enforcement", unlike dmarc.py's own checker which reports those
    two cases differently to the user). Used by registry.py to decide
    whether SPF's conditional ~all/-all preference applies — kept separate
    from dmarc.py's _check_inherited_policy, which needs the same walk but
    produces user-facing Findings with more granular error messages."""
    try:
        record = await fetch_current_dmarc_record(domain)
    except DnsLookupError:
        record = None
    if record is not None:
        return record.tags.get("p", "").lower() or None

    if not parent_domain:
        return None
    try:
        parent_record = await fetch_current_dmarc_record(parent_domain)
    except DnsLookupError:
        return None
    if parent_record is None:
        return None
    return (parent_record.tags.get("sp") or parent_record.tags.get("p") or "").lower() or None


@dataclasses.dataclass
class RuaDestinationCheck:
    status: str  # "not_configured" | "lookup_error" | "no_rua" | "points_elsewhere" | "correct"
    current_targets: list[str]


async def check_rua_destination(domain: str, mailbox_address: str) -> RuaDestinationCheck:
    """Does this domain's published rua= actually include the org's
    connected mailbox? A domain can be fully "verified" and have a clean
    DNS baseline while still never sending this product a single report,
    if rua= was never pointed here (or was later changed) — the kind of
    gap that's easy to miss without an explicit check for it."""
    try:
        record = await fetch_current_dmarc_record(domain)
    except DnsLookupError:
        return RuaDestinationCheck(status="lookup_error", current_targets=[])
    if record is None:
        return RuaDestinationCheck(status="not_configured", current_targets=[])
    targets = parse_mailto_targets(record.tags.get("rua", ""))
    if not targets:
        return RuaDestinationCheck(status="no_rua", current_targets=[])
    if mailbox_address.lower() in (t.lower() for t in targets):
        return RuaDestinationCheck(status="correct", current_targets=targets)
    return RuaDestinationCheck(status="points_elsewhere", current_targets=targets)
