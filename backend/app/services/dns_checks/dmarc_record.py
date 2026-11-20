"""Shared DMARC record parsing + live-fetch helpers, used by dmarc.py's
best-practice checker AND by onboarding/the policy builder (which need to
read + compare a domain's current published record, not just lint it).
Pulled out into its own module so both sides call one implementation."""

import dataclasses
import re

from app.services.dns_checks.resolver import DnsLookupError, resolve_txt_strict
from app.services.report_destination import ReportDestination

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


_VALID_POLICIES = ("none", "quarantine", "reject")


@dataclasses.dataclass(frozen=True)
class EffectivePolicy:
    policy: str | None  # None = no usable value (absent all the way up, or invalid)
    inherited: bool


def effective_policies(tags: dict[str, str]) -> dict[str, EffectivePolicy]:
    """Effective p/sp/np for a published record's tags (RFC 7489/9989): an
    absent or empty sp= inherits p=, an absent or empty np= inherits sp=; a
    present but invalid value is unknown, not inherited. The frontend
    Policy Builder applies the same rules (dmarcInheritance.ts) — both are
    tested against shared/dmarc-inheritance-cases.json."""

    def resolve(raw: str | None, parent: EffectivePolicy) -> EffectivePolicy:
        if not raw:
            return EffectivePolicy(parent.policy, inherited=True)
        value = raw.lower()
        return EffectivePolicy(value if value in _VALID_POLICIES else None, inherited=False)

    p_raw = (tags.get("p") or "").lower()
    p = EffectivePolicy(p_raw if p_raw in _VALID_POLICIES else None, inherited=False)
    sp = resolve(tags.get("sp"), p)
    return {"p": p, "sp": sp, "np": resolve(tags.get("np"), sp)}


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


# RFC 9989 §4.10: a name of 8 or more labels jumps straight to its 7
# rightmost labels, so the walk never takes more than 8 lookups.
_TREE_WALK_MAX_LABELS = 7


def tree_walk_targets(domain: str) -> list[str]:
    """The names a receiver tries, nearest first, when <domain> has no DMARC
    record of its own (RFC 9989 §4.10 DNS tree walk): drop one label at a
    time, up to and including the top-level domain. No Public Suffix List:
    gtest.example.co.uk walks example.co.uk, co.uk, uk."""
    labels = domain.rstrip(".").lower().split(".")
    labels = labels[-_TREE_WALK_MAX_LABELS:] if len(labels) > _TREE_WALK_MAX_LABELS else labels[1:]
    return [".".join(labels[i:]) for i in range(len(labels))]


@dataclasses.dataclass(frozen=True)
class InheritedPolicy:
    from_domain: str
    policy: str | None  # the record's effective sp= — None when missing or invalid
    tag: str  # "sp" or "p": where the policy came from
    rua: list[str]  # reports about this domain go to that record's rua=


async def fetch_inherited_policy(domain: str) -> InheritedPolicy | None:
    """The policy a domain without a record of its own gets today: the
    first DMARC record up the tree (see tree_walk_targets), wherever it
    lives — the parent may be in another organization or not in this app at
    all. None when no record is found up to the top-level domain. Raises
    DnsLookupError like fetch_current_dmarc_record. Only call it for a
    domain confirmed to have no record of its own."""
    for target in tree_walk_targets(domain):
        record = await fetch_current_dmarc_record(target)
        if record is None:
            continue
        sp = effective_policies(record.tags)["sp"]
        return InheritedPolicy(
            from_domain=target,
            policy=sp.policy,
            tag="p" if sp.inherited else "sp",
            rua=parse_mailto_targets(record.tags.get("rua", "")),
        )
    return None


async def resolve_effective_policy(domain: str) -> str | None:
    """The enforcement policy actually covering this domain's mail: its own
    p=, or — if it has no DMARC record of its own, the normal case for a
    subdomain — the sp= (preferred) or p= of the first record up the tree.
    Returns None if nothing resolvable either way (including on a lookup
    failure; callers here only need "is DMARC actually enforcing yet",
    where a failed lookup and a confirmed-absent record both mean "can't
    credit enforcement", unlike dmarc.py's own checker which reports those
    two cases differently to the user). Used by registry.py to decide
    whether SPF's conditional ~all/-all preference applies."""
    try:
        record = await fetch_current_dmarc_record(domain)
    except DnsLookupError:
        record = None
    if record is not None:
        return record.tags.get("p", "").lower() or None
    try:
        inherited = await fetch_inherited_policy(domain)
    except DnsLookupError:
        return None
    return inherited.policy if inherited is not None else None


@dataclasses.dataclass
class RuaDestinationCheck:
    status: str  # "not_configured" | "lookup_error" | "no_rua" | "points_elsewhere" | "correct"
    current_targets: list[str]


async def check_rua_destination(domain: str, destination: ReportDestination) -> RuaDestinationCheck:
    """Does this domain's published rua= actually include the org's
    report destination? A domain can be fully "verified" and have a clean
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
    if destination.reached_by(targets):
        return RuaDestinationCheck(status="correct", current_targets=targets)
    return RuaDestinationCheck(status="points_elsewhere", current_targets=targets)
