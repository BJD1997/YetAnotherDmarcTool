"""Per-sending-service DMARC volume/alignment breakdown for a domain.
Extracted out of the dmarc_reports router so it has a single implementation
shared by /domains/{id}/dmarc/sources, /domains/{id}/dmarc/sender-inventory,
and the action-queue's unknown_sender_above_threshold rule — all three need
the exact same "aggregate per source_ip, identify via PTR, roll up by
service" computation, not three copies of it."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dmarc_reports import per_source_ip_volume_breakdown
from app.services.source_identification.service_identifier import identify_many


def _is_ipv6(ip: str) -> bool:
    # source_ip is stored as INET and rendered back as text; only IPv6
    # literals contain a colon, IPv4 never does.
    return ":" in ip

# A service is "likely spoofed" when the receiver mostly didn't trust it
# (quarantined/rejected) AND it's essentially unauthenticated on both
# mechanisms — the strongest available signal that this isn't a legitimate
# sender that just needs SPF/DKIM configured, but unauthorized use of the
# domain that the existing policy is already correctly blocking.
SPOOFED_BLOCKED_MIN_PCT = 90.0
SPOOFED_ALIGNED_MAX_PCT = 5.0


def _is_likely_spoofed(volume: int, quarantined: int, rejected: int, spf_aligned_pct: float | None, dkim_aligned_pct: float | None) -> bool:
    if volume == 0:
        return False
    blocked_pct = (quarantined + rejected) / volume * 100
    if blocked_pct < SPOOFED_BLOCKED_MIN_PCT:
        return False
    return (spf_aligned_pct or 0) <= SPOOFED_ALIGNED_MAX_PCT and (dkim_aligned_pct or 0) <= SPOOFED_ALIGNED_MAX_PCT


def _fcrdns_status(ip_rows: list[dict]) -> str:
    """Roll a service's per-IP forward-confirmed-reverse-DNS states up into one
    label: "pass" (every sending IP forward-confirms), "partial" (some do, some
    don't), or "fail" (none do — whether because their PTR doesn't point back or
    there's no PTR at all). Per-IP fcrdns_valid keeps the finer no-PTR (None) vs
    bad-PTR (False) distinction for the expanded view; this is the at-a-glance
    roll-up used to flag a sender the operator has claimed as their own."""
    states = [ip["fcrdns_valid"] for ip in ip_rows]
    has_pass = any(v is True for v in states)
    has_unconfirmed = any(v is not True for v in states)  # False (bad PTR) or None (no PTR)
    if has_pass and not has_unconfirmed:
        return "pass"
    if has_pass and has_unconfirmed:
        return "partial"
    return "fail"


async def service_breakdown(db: AsyncSession, domain_id: uuid.UUID, *, since: datetime | None = None) -> list[dict]:
    """Volume/alignment/disposition aggregated per-IP in SQL, then grouped by
    identified service label in Python (simpler and more testable than an
    awkward GROUP BY over a value that only exists after a DNS lookup).

    `since` windows the breakdown to reports whose traffic period begins on or
    after it (joining the parent report's date_range_begin, the same recency
    basis dmarc_trend uses) — so a decommissioned host or a retired sender with
    no recent traffic simply drops out of the inventory. None = all-time.

    Does NOT commit — identify_many's cache upsert is executed but left
    uncommitted, deliberately. A commit here would end the calling request's
    SET LOCAL app.current_org_id scope (see app/db/rls.py), silently
    breaking RLS for any query the caller runs afterward — which several
    callers do (sender_inventory queries sender_reviews right after this;
    the action-queue router calls this once per domain in a loop, and a
    mid-loop commit would drop RLS context for every later iteration).
    Callers must commit once, themselves, after all their own RLS-scoped
    work for the request is done."""
    rows = await per_source_ip_volume_breakdown(db, domain_id, since=since)
    per_ip = [
        {
            "source_ip": str(ip),
            "volume": int(volume),
            "spf_pass": int(spf_pass),
            "dkim_pass": int(dkim_pass),
            "dmarc_pass": int(dmarc_pass_count),
            "accepted": int(accepted),
            "quarantined": int(quarantined),
            "rejected": int(rejected),
        }
        for ip, volume, spf_pass, dkim_pass, dmarc_pass_count, accepted, quarantined, rejected in rows
    ]
    if not per_ip:
        return []

    identities = await identify_many(db, [row["source_ip"] for row in per_ip])

    def _pct(n: int, d: int) -> float | None:
        return round(n / d * 100, 1) if d else None

    by_service: dict[str, dict] = {}
    for row in per_ip:
        identity = identities[row["source_ip"]]
        row["ptr_hostname"] = identity.ptr_hostname
        row["fcrdns_valid"] = identity.fcrdns_valid
        bucket = by_service.setdefault(
            identity.service_label,
            {
                "service_label": identity.service_label,
                "match_method": identity.match_method.value,
                "volume": 0,
                "source_ip_count": 0,
                "spf_pass": 0,
                "dkim_pass": 0,
                "dmarc_pass": 0,
                "accepted": 0,
                "quarantined": 0,
                "rejected": 0,
                "ips": [],  # per-IP rows, percentages filled in below once totals are known
            },
        )
        bucket["volume"] += row["volume"]
        bucket["source_ip_count"] += 1
        for key in ("spf_pass", "dkim_pass", "dmarc_pass", "accepted", "quarantined", "rejected"):
            bucket[key] += row[key]
        bucket["ips"].append(row)

    def _service_dict(b: dict) -> dict:
        spf_aligned_pct = _pct(b["spf_pass"], b["volume"])
        dkim_aligned_pct = _pct(b["dkim_pass"], b["volume"])
        v4_ips = [ip for ip in b["ips"] if not _is_ipv6(ip["source_ip"])]
        v6_ips = [ip for ip in b["ips"] if _is_ipv6(ip["source_ip"])]
        return {
            "service_label": b["service_label"],
            "match_method": b["match_method"],
            "volume": b["volume"],
            "source_ip_count": b["source_ip_count"],
            "spf_aligned_pct": spf_aligned_pct,
            "dkim_aligned_pct": dkim_aligned_pct,
            "dmarc_pass_pct": _pct(b["dmarc_pass"], b["volume"]),
            "accepted": b["accepted"],
            "quarantined": b["quarantined"],
            "rejected": b["rejected"],
            "likely_spoofed": _is_likely_spoofed(b["volume"], b["quarantined"], b["rejected"], spf_aligned_pct, dkim_aligned_pct),
            "fcrdns_status": _fcrdns_status(b["ips"]),
            # Per-family roll-ups too, since IPv6-without-valid-rDNS is the case
            # Gmail/Microsoft actually junk or reject (v4 is far more forgiving),
            # and a host's v4 and v6 can differ. None = no IPs of that family.
            "fcrdns_status_v4": _fcrdns_status(v4_ips) if v4_ips else None,
            "fcrdns_status_v6": _fcrdns_status(v6_ips) if v6_ips else None,
            "sends_ipv6": bool(v6_ips),
            "source_ips": sorted(
                (
                    {
                        "source_ip": ip_row["source_ip"],
                        "is_ipv6": _is_ipv6(ip_row["source_ip"]),
                        "ptr_hostname": ip_row["ptr_hostname"],
                        "fcrdns_valid": ip_row["fcrdns_valid"],
                        "volume": ip_row["volume"],
                        "spf_aligned_pct": _pct(ip_row["spf_pass"], ip_row["volume"]),
                        "dkim_aligned_pct": _pct(ip_row["dkim_pass"], ip_row["volume"]),
                        "accepted": ip_row["accepted"],
                        "quarantined": ip_row["quarantined"],
                        "rejected": ip_row["rejected"],
                    }
                    for ip_row in b["ips"]
                ),
                key=lambda r: -r["volume"],
            ),
        }

    services = [_service_dict(b) for b in by_service.values()]
    services.sort(key=lambda s: -s["volume"])
    return services
