import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.domain import Domain
from app.models.enums import AuthResult
from app.models.tls_rpt import TlsRptReport
from app.models.user import User

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
    domain_id: uuid.UUID,
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    await _get_owned_domain(db, domain_id, user.organization_id)

    result = await db.execute(
        select(DmarcAggregateRecord.source_ip, DmarcAggregateRecord.header_from, func.sum(DmarcAggregateRecord.count))
        .where(DmarcAggregateRecord.domain_id == domain_id)
        .group_by(DmarcAggregateRecord.source_ip, DmarcAggregateRecord.header_from)
        .order_by(func.sum(DmarcAggregateRecord.count).desc())
        .limit(limit)
    )
    return [
        {"source_ip": str(ip), "header_from": header_from, "count": int(count)}
        for ip, header_from, count in result.all()
    ]


@router.get("/domains/{domain_id}/dmarc/reports")
async def dmarc_reports(
    domain_id: uuid.UUID,
    limit: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    await _get_owned_domain(db, domain_id, user.organization_id)

    result = await db.execute(
        select(DmarcAggregateReport)
        .where(DmarcAggregateReport.domain_id == domain_id)
        .order_by(DmarcAggregateReport.date_range_begin.desc())
        .limit(limit)
    )
    return [
        {
            "id": str(r.id),
            "org_name": r.org_name,
            "report_id": r.report_id,
            "date_range_begin": r.date_range_begin.isoformat(),
            "date_range_end": r.date_range_end.isoformat(),
            "policy_p": r.policy_p,
            "policy_pct": r.policy_pct,
            "received_at": r.received_at.isoformat(),
        }
        for r in result.scalars().all()
    ]


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
