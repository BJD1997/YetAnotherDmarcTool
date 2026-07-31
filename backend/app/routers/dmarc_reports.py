import itertools
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition
from app.models.tls_rpt import TlsRptReport
from app.models.user import User
from app.services.dmarc_narrative import dkim_narratives, spf_narratives
from app.services.source_identification.service_identifier import identify_many

router = APIRouter(tags=["dmarc-reports"])


async def _get_owned_domain(db: AsyncSession, domain_id: uuid.UUID, organization_id: uuid.UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return domain


@router.get("/domains/{domain_id}/dmarc/summary")
async def dmarc_summary(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    await _get_owned_domain(db, domain_id, user.organization_id)

    # A message passes DMARC if EITHER SPF or DKIM is aligned-pass (RFC 7489)
    # — policy_evaluated.{dkim,spf} in the aggregate report already reflect
    # the receiver's own alignment-aware judgement, so no separate
    # "alignment" bookkeeping is needed beyond what's already stored.
    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    totals = await db.execute(
        select(
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
            func.coalesce(func.sum(case((dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0),
        ).where(DmarcAggregateRecord.domain_id == domain_id)
    )
    total_count, pass_count = totals.one()

    disposition_rows = await db.execute(
        select(DmarcAggregateRecord.disposition, func.sum(DmarcAggregateRecord.count))
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.disposition)
    )
    by_disposition = {disposition.value: count for disposition, count in disposition_rows.all()}

    report_count = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.domain_id == domain_id)
    )

    return {
        "total_message_count": int(total_count),
        "dmarc_pass_count": int(pass_count),
        "dmarc_fail_count": int(total_count) - int(pass_count),
        "by_disposition": by_disposition,
        "report_count": report_count.scalar_one(),
    }


@router.get("/domains/{domain_id}/dmarc/sources")
async def dmarc_sources(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Per-sending-service breakdown (not per raw source_ip — header_from is
    constant per domain so grouping by it, as this endpoint used to, added
    nothing; source_ip alone is the real grouping key, further rolled up by
    identified sending service). Volume/alignment/disposition are aggregated
    per-IP in SQL, then grouped by service label in Python (simpler and more
    testable than an awkward GROUP BY over a value that only exists after a
    DNS lookup)."""
    await _get_owned_domain(db, domain_id, user.organization_id)

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
    await db.commit()  # persists any newly-resolved source_ip_identities cache rows

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
            },
        )
        bucket["volume"] += row["volume"]
        bucket["source_ip_count"] += 1
        for key in ("spf_pass", "dkim_pass", "dmarc_pass", "accepted", "quarantined", "rejected"):
            bucket[key] += row[key]

    services = [
        {
            "service_label": b["service_label"],
            "match_method": b["match_method"],
            "volume": b["volume"],
            "source_ip_count": b["source_ip_count"],
            "spf_aligned_pct": _pct(b["spf_pass"], b["volume"]),
            "dkim_aligned_pct": _pct(b["dkim_pass"], b["volume"]),
            "dmarc_pass_pct": _pct(b["dmarc_pass"], b["volume"]),
            "accepted": b["accepted"],
            "quarantined": b["quarantined"],
            "rejected": b["rejected"],
        }
        for b in by_service.values()
    ]
    services.sort(key=lambda s: -s["volume"])
    return services


@router.get("/domains/{domain_id}/dmarc/reports/by-day")
async def dmarc_reports_by_day(
    domain_id: uuid.UUID,
    limit: int = Query(300, ge=1, le=1000),
    before_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Row granularity is one DmarcAggregateRecord (one sending host within
    one report), not one whole report — a report with several source IPs
    shows as several rows on its day. Keyset-paginated on
    (date_range_begin, record id) rather than offset, so pages stay stable
    as new reports keep arriving between requests."""
    await _get_owned_domain(db, domain_id, user.organization_id)

    query = (
        select(
            DmarcAggregateRecord.id,
            DmarcAggregateReport.id.label("report_pk"),
            DmarcAggregateReport.org_name,
            DmarcAggregateReport.date_range_begin,
            DmarcAggregateRecord.source_ip,
            DmarcAggregateRecord.count,
            DmarcAggregateRecord.disposition,
            DmarcAggregateRecord.spf_result,
            DmarcAggregateRecord.dkim_result,
        )
        .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
        .where(DmarcAggregateRecord.domain_id == domain_id)
    )

    if before_id is not None:
        anchor = (
            await db.execute(
                select(DmarcAggregateReport.date_range_begin, DmarcAggregateRecord.id)
                .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
                .where(DmarcAggregateRecord.id == before_id, DmarcAggregateRecord.domain_id == domain_id)
            )
        ).first()
        if anchor is not None:
            query = query.where(tuple_(DmarcAggregateReport.date_range_begin, DmarcAggregateRecord.id) < anchor)

    query = query.order_by(DmarcAggregateReport.date_range_begin.desc(), DmarcAggregateRecord.id.desc()).limit(limit)
    rows = (await db.execute(query)).all()

    days = []
    for date, group_iter in itertools.groupby(rows, key=lambda r: r.date_range_begin.date()):
        group = list(group_iter)
        report_ids = {r.report_pk for r in group}
        days.append(
            {
                "date": date.isoformat(),
                "report_count": len(report_ids),
                "message_count": sum(r.count for r in group),
                "accepted": sum(r.count for r in group if r.disposition == Disposition.none),
                "quarantined": sum(r.count for r in group if r.disposition == Disposition.quarantine),
                "rejected": sum(r.count for r in group if r.disposition == Disposition.reject),
                "rows": [
                    {
                        "record_id": str(r.id),
                        "org_name": r.org_name,
                        "source_ip": str(r.source_ip),
                        "count": r.count,
                        "disposition": r.disposition.value,
                        "spf_result": r.spf_result.value,
                        "dkim_result": r.dkim_result.value,
                    }
                    for r in group
                ],
            }
        )

    return {"days": days, "has_more": len(rows) == limit}


@router.get("/domains/{domain_id}/dmarc/records/{record_id}")
async def dmarc_record_detail(
    domain_id: uuid.UUID,
    record_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    await _get_owned_domain(db, domain_id, user.organization_id)

    row = (
        await db.execute(
            select(DmarcAggregateRecord, DmarcAggregateReport)
            .join(DmarcAggregateReport, DmarcAggregateReport.id == DmarcAggregateRecord.report_id)
            .where(DmarcAggregateRecord.id == record_id, DmarcAggregateRecord.domain_id == domain_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")
    record, report = row

    return {
        "id": str(record.id),
        "report": {
            "id": str(report.id),
            "report_id": report.report_id,
            "org_name": report.org_name,
            "email": report.email,
            "date_range_begin": report.date_range_begin.isoformat(),
            "date_range_end": report.date_range_end.isoformat(),
            "policy_p": report.policy_p,
            "policy_sp": report.policy_sp,
            "policy_pct": report.policy_pct,
            "policy_adkim": report.policy_adkim,
            "policy_aspf": report.policy_aspf,
        },
        "source_ip": str(record.source_ip),
        "count": record.count,
        "disposition": record.disposition.value,
        "spf_result": record.spf_result.value,
        "dkim_result": record.dkim_result.value,
        "header_from": record.header_from,
        "envelope_from": record.envelope_from,
        "envelope_to": record.envelope_to,
        "auth_results": record.auth_results,
        "spf_narrative": spf_narratives(record.auth_results, str(record.source_ip), record.header_from),
        "dkim_narrative": dkim_narratives(record.auth_results, record.header_from),
        "verdict": {
            "spf_aligned": record.spf_result == AuthResult.pass_,
            "dkim_aligned": record.dkim_result == AuthResult.pass_,
            "dmarc_aligned": record.spf_result == AuthResult.pass_ or record.dkim_result == AuthResult.pass_,
            "disposition_applied": record.disposition.value,
        },
    }


@router.get("/dmarc/unmatched")
async def unmatched_reports(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Aggregate reports whose policy_published domain didn't match any
    registered Domain in this org — surfaced rather than silently dropped
    (see domain_matcher.py)."""
    result = await db.execute(
        select(DmarcAggregateReport)
        .where(DmarcAggregateReport.organization_id == user.organization_id, DmarcAggregateReport.domain_id.is_(None))
        .order_by(DmarcAggregateReport.received_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(r.id),
            "org_name": r.org_name,
            "report_id": r.report_id,
            "policy_published_domain": r.policy_published_domain,
            "received_at": r.received_at.isoformat(),
        }
        for r in result.scalars().all()
    ]


@router.get("/dmarc/detected-domains")
async def detected_domains(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """Distinct domain names seen in reports that didn't match anything
    registered — the same unmatched bucket as /dmarc/unmatched, but grouped
    by domain name (across aggregate, TLS-RPT, and forensic reports) so it
    reads as "domains you could add" rather than a raw report list. Each
    entry is annotated with whether it looks like a subdomain of a domain
    you've already registered (in which case "Add" can create it correctly
    parented in one step) or of another *detected* domain (informational —
    our one-level nesting model means the detected apex should generally be
    added first)."""
    detected: dict[str, dict] = {}

    agg_result = await db.execute(
        select(
            DmarcAggregateReport.policy_published_domain,
            func.count(func.distinct(DmarcAggregateReport.id)),
            func.coalesce(func.sum(DmarcAggregateRecord.count), 0),
        )
        .outerjoin(DmarcAggregateRecord, DmarcAggregateRecord.report_id == DmarcAggregateReport.id)
        .where(
            DmarcAggregateReport.organization_id == user.organization_id,
            DmarcAggregateReport.domain_id.is_(None),
        )
        .group_by(DmarcAggregateReport.policy_published_domain)
    )
    for name, report_count, message_volume in agg_result.all():
        detected[name] = {"report_count": report_count, "message_volume": int(message_volume)}

    tls_result = await db.execute(
        select(
            TlsRptReport.policy_domain,
            func.count(func.distinct(TlsRptReport.id)),
            func.coalesce(
                func.sum(TlsRptReport.summary_success_count + TlsRptReport.summary_failure_count), 0
            ),
        )
        .where(TlsRptReport.organization_id == user.organization_id, TlsRptReport.domain_id.is_(None))
        .group_by(TlsRptReport.policy_domain)
    )
    for name, report_count, message_volume in tls_result.all():
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count
        entry["message_volume"] += int(message_volume)

    forensic_result = await db.execute(
        select(DmarcForensicReport.reported_domain, func.count())
        .where(
            DmarcForensicReport.organization_id == user.organization_id,
            DmarcForensicReport.domain_id.is_(None),
            DmarcForensicReport.reported_domain.is_not(None),
            DmarcForensicReport.reported_domain != "",
        )
        .group_by(DmarcForensicReport.reported_domain)
    )
    for name, report_count in forensic_result.all():
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count

    registered_result = await db.execute(
        select(Domain.id, Domain.name).where(Domain.organization_id == user.organization_id)
    )
    registered = {name: domain_id for domain_id, name in registered_result.all()}
    detected_names = set(detected.keys())

    items = []
    for name, stats in detected.items():
        suggested_parent_id: str | None = None
        suggested_parent_name: str | None = None
        relationship = "apex"

        # Prefer the longest (most specific) registered ancestor, in case
        # more than one registered domain is a suffix match.
        for reg_name, reg_id in sorted(registered.items(), key=lambda kv: -len(kv[0])):
            if name.endswith(f".{reg_name}"):
                suggested_parent_id = str(reg_id)
                suggested_parent_name = reg_name
                relationship = "subdomain_of_registered"
                break

        if suggested_parent_id is None:
            for other in sorted(detected_names, key=len):
                if other != name and name.endswith(f".{other}"):
                    relationship = "subdomain_of_detected"
                    suggested_parent_name = other
                    break

        items.append(
            {
                "name": name,
                "report_count": stats["report_count"],
                "message_volume": stats["message_volume"],
                "relationship": relationship,
                "suggested_parent_id": suggested_parent_id,
                "suggested_parent_name": suggested_parent_name,
            }
        )

    # Apex-looking entries first, then shorter (more likely-apex) names first.
    items.sort(key=lambda x: (x["relationship"] != "apex", len(x["name"]), -x["report_count"]))
    return items
