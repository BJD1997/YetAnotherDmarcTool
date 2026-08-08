"""Per-sending-service DMARC volume/alignment breakdown for a domain.
Extracted out of the dmarc_reports router so it has a single implementation
shared by /domains/{id}/dmarc/sources, /domains/{id}/dmarc/sender-inventory,
and the action-queue's unknown_sender_above_threshold rule — all three need
the exact same "aggregate per source_ip, identify via PTR, roll up by
service" computation, not three copies of it."""

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateRecord
from app.models.enums import AuthResult, Disposition
from app.services.source_identification.service_identifier import identify_many

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


async def service_breakdown(db: AsyncSession, domain_id: uuid.UUID) -> list[dict]:
    """Volume/alignment/disposition aggregated per-IP in SQL, then grouped by
    identified service label in Python (simpler and more testable than an
    awkward GROUP BY over a value that only exists after a DNS lookup).

    Does NOT commit — identify_many's cache upsert is executed but left
    uncommitted, deliberately. A commit here would end the calling request's
    SET LOCAL app.current_org_id scope (see app/db/rls.py), silently
    breaking RLS for any query the caller runs afterward — which several
    callers do (sender_inventory queries sender_reviews right after this;
    the action-queue router calls this once per domain in a loop, and a
    mid-loop commit would drop RLS context for every later iteration).
    Callers must commit once, themselves, after all their own RLS-scoped
    work for the request is done."""
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    def _sum_where(condition):
        return func.sum(case((condition, DmarcAggregateRecord.count), else_=0))

    result = await db.execute(
        select(
            DmarcAggregateRecord.source_ip,
            func.sum(DmarcAggregateRecord.count),
            _sum_where(DmarcAggregateRecord.spf_result == AuthResult.pass_),
            _sum_where(DmarcAggregateRecord.dkim_result == AuthResult.pass_),
            _sum_where(dmarc_pass),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.none),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.quarantine),
            _sum_where(DmarcAggregateRecord.disposition == Disposition.reject),
        )
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.source_ip)
    )
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
        for ip, volume, spf_pass, dkim_pass, dmarc_pass_count, accepted, quarantined, rejected in result.all()
    ]
    if not per_ip:
        return []

    identities = await identify_many(db, [row["source_ip"] for row in per_ip])

    def _pct(n: int, d: int) -> float | None:
        return round(n / d * 100, 1) if d else None

    by_service: dict[str, dict] = {}
    for row in per_ip:
        identity = identities[row["source_ip"]]
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
            "source_ips": sorted(
                (
                    {
                        "source_ip": ip_row["source_ip"],
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
