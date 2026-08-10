import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.tls_rpt import TlsRptReport
from app.models.user import User
from app.services.dns_checks.base import is_null_mx
from app.services.dns_checks.dmarc_record import check_rua_destination
from app.services.dns_checks.inbound_view import build_inbound_hosts
from app.services.dns_checks.mta_sts import _TXT_RE, _mx_covered, _parse_policy, fetch_policy_file
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx, resolve_txt_strict
from app.services.dns_checks.scheduled_recheck import run_and_persist_checks
from app.services.dns_checks.tls_rpt_check import check_tls_rpt_rua_destination, fetch_current_tls_rpt_record

router = APIRouter(tags=["dns-checks"])


async def _get_owned_domain(db: AsyncSession, domain_id: uuid.UUID, organization_id: uuid.UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return domain


def _result_out(r: DnsCheckResult) -> dict:
    return {
        "id": str(r.id),
        "check_type": r.check_type.value,
        "subject": r.subject,
        "status": r.status.value,
        "summary": r.summary,
        "details": r.details,
        "rule_version": r.rule_version,
        "checked_at": r.checked_at.isoformat(),
    }


@router.get("/domains/{domain_id}/checks")
async def list_latest_checks(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    await _get_owned_domain(db, domain_id, user.organization_id)

    # NOT distinct-on (check_type, subject): a single check_type routinely
    # produces several findings sharing the same subject (SPF's lookup-count
    # finding and its 'all'-qualifier finding both have subject=NULL, same
    # for DMARC's several structural notes) — DISTINCT ON would silently
    # collapse those down to one row each. Every row from one recheck() call
    # shares the exact same checked_at (set once per call, see
    # recheck_domain below), so "the latest run's results" is simply every
    # row at the max checked_at for this domain.
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at))
        .where(DnsCheckResult.domain_id == domain_id)
        .scalar_subquery()
    )
    result = await db.execute(
        select(DnsCheckResult)
        .where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.checked_at == latest_ts)
        .order_by(DnsCheckResult.check_type, DnsCheckResult.subject.nulls_first())
    )
    return [_result_out(r) for r in result.scalars().all()]


@router.get("/domains/{domain_id}/dmarc/inbound")
async def inbound_hosts(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    await _get_owned_domain(db, domain_id, user.organization_id)
    rows = await build_inbound_hosts(db, domain_id)
    return [
        {
            "host": r.host,
            "priority": r.priority,
            "provider_label": r.provider_label,
            "mx_status": r.mx_status,
            "starttls_status": r.starttls_status,
            "dane_status": r.dane_status,
            "mta_sts_status": r.mta_sts_status,
        }
        for r in rows
    ]


async def _fetch_tls_rpt_rows(
    db: AsyncSession,
    domain_id: uuid.UUID,
    *,
    days: int | None,
    org_name: str | None,
    result_type: str | None,
    failures_only: bool,
) -> list[dict]:
    """Shared filter/fetch for all three /dmarc/tls-rpt/* endpoints below —
    same _apply_report_filters-style vocabulary the DMARC reports endpoints
    use (dmarc_reports.py), applied to RFC 8460 SMTP TLS reports. days/
    org_name/failures_only filter in SQL; result_type filters in Python
    after fetch (failure_details is a JSONB array — filtering its contents
    in SQL needs jsonb_array_elements, not worth it at this domain's real
    data volume: hundreds of rows over years, not raw message counts).
    A row that qualifies on result_type still returns its full
    failure_details, not just the matching entries — the point of drilling
    into one report is seeing everything it said, not a pre-filtered slice.
    Returns newest-first."""
    query = select(TlsRptReport).where(TlsRptReport.domain_id == domain_id)
    if days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.where(TlsRptReport.date_range_begin >= since)
    if org_name is not None:
        query = query.where(TlsRptReport.org_name.ilike(f"%{org_name}%"))
    if failures_only:
        query = query.where(TlsRptReport.summary_failure_count > 0)
    query = query.order_by(TlsRptReport.date_range_begin.desc())

    reports = (await db.execute(query)).scalars().all()

    rows = []
    for r in reports:
        failure_details = r.failure_details if isinstance(r.failure_details, list) else []
        if result_type is not None and not any(item.get("result_type") == result_type for item in failure_details):
            continue
        rows.append(
            {
                "id": str(r.id),
                "org_name": r.org_name,
                "policy_type": r.policy_type.value,
                "date_range_begin": r.date_range_begin.isoformat(),
                "date_range_end": r.date_range_end.isoformat(),
                "successful_session_count": r.summary_success_count,
                "failed_session_count": r.summary_failure_count,
                "failure_details": failure_details,
            }
        )
    return rows


@router.get("/domains/{domain_id}/dmarc/tls-rpt/summary")
async def tls_rpt_summary(
    domain_id: uuid.UUID,
    days: int | None = Query(None, ge=1, le=3650),
    org_name: str | None = Query(None),
    result_type: str | None = Query(None),
    failures_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    await _get_owned_domain(db, domain_id, user.organization_id)
    rows = await _fetch_tls_rpt_rows(
        db, domain_id, days=days, org_name=org_name, result_type=result_type, failures_only=failures_only
    )

    total_success = sum(r["successful_session_count"] for r in rows)
    total_failure = sum(r["failed_session_count"] for r in rows)
    total_sessions = total_success + total_failure
    failure_rate_pct = round(total_failure / total_sessions * 100, 1) if total_sessions else None

    return {
        "total_reports": len(rows),
        "total_successful_sessions": total_success,
        "total_failed_sessions": total_failure,
        "failure_rate_pct": failure_rate_pct,
        "distinct_reporting_orgs": len({r["org_name"] for r in rows}),
        "last_report_received_at": rows[0]["date_range_begin"] if rows else None,
        "policy_type": rows[0]["policy_type"] if rows else None,
    }


@router.get("/domains/{domain_id}/dmarc/tls-rpt/reports")
async def tls_rpt_reports(
    domain_id: uuid.UUID,
    days: int | None = Query(None, ge=1, le=3650),
    org_name: str | None = Query(None),
    result_type: str | None = Query(None),
    failures_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Row granularity is one TlsRptReport (one policy-domain within one
    report) — no further pagination, unlike the DMARC equivalents, since
    real volume here doesn't need it (see _fetch_tls_rpt_rows)."""
    await _get_owned_domain(db, domain_id, user.organization_id)
    return await _fetch_tls_rpt_rows(
        db, domain_id, days=days, org_name=org_name, result_type=result_type, failures_only=failures_only
    )


@router.get("/domains/{domain_id}/dmarc/tls-rpt/by-sender")
async def tls_rpt_by_sender(
    domain_id: uuid.UUID,
    days: int | None = Query(None, ge=1, le=3650),
    org_name: str | None = Query(None),
    result_type: str | None = Query(None),
    failures_only: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    await _get_owned_domain(db, domain_id, user.organization_id)
    rows = await _fetch_tls_rpt_rows(
        db, domain_id, days=days, org_name=org_name, result_type=result_type, failures_only=failures_only
    )

    senders: dict[str, dict] = {}
    for r in rows:
        entry = senders.setdefault(
            r["org_name"],
            {"org_name": r["org_name"], "successful_session_count": 0, "failed_session_count": 0, "failure_reasons": {}},
        )
        entry["successful_session_count"] += r["successful_session_count"]
        entry["failed_session_count"] += r["failed_session_count"]
        for item in r["failure_details"]:
            rt = item.get("result_type")
            if not rt:
                continue
            entry["failure_reasons"][rt] = entry["failure_reasons"].get(rt, 0) + (item.get("failed_session_count") or 0)

    senders_out = []
    for entry in senders.values():
        reasons = [{"result_type": rt, "count": c} for rt, c in entry["failure_reasons"].items()]
        reasons.sort(key=lambda x: x["count"], reverse=True)
        senders_out.append(
            {
                "org_name": entry["org_name"],
                "successful_session_count": entry["successful_session_count"],
                "failed_session_count": entry["failed_session_count"],
                "failure_reasons": reasons,
            }
        )
    senders_out.sort(key=lambda s: s["failed_session_count"], reverse=True)
    return senders_out


@router.get("/domains/{domain_id}/dmarc/rua-check")
async def dmarc_rua_check(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """Does this domain's published rua= actually point at its report
    destination — its own hosted address if one's been generated (see
    POST /domains/{id}/hosted-report-address), else the org's connected
    mailbox? A domain can be verified with a clean DNS baseline and still
    never send this product a single report if rua= was never set (or was
    later changed) to point here — see dmarc_record.py."""
    domain = await _get_owned_domain(db, domain_id, user.organization_id)
    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == user.organization_id))
    ).scalar_one_or_none()
    mailbox_address = domain.hosted_report_address or (connection.mailbox_address if connection else None)
    if mailbox_address is None:
        return {"status": "no_mailbox", "current_targets": [], "org_mailbox_address": None}

    result = await check_rua_destination(domain.name, mailbox_address)
    return {
        "status": result.status,
        "current_targets": result.current_targets,
        "org_mailbox_address": mailbox_address,
    }


@router.post("/domains/{domain_id}/checks/recheck")
async def recheck_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> list[dict]:
    domain = await _get_owned_domain(db, domain_id, user.organization_id)

    if domain.verification_status != DomainVerificationStatus.verified:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "domain must be verified before best-practice checks can run"
        )

    org = await db.get(Organization, user.organization_id)
    rows = await run_and_persist_checks(db, domain, org)
    await db.commit()

    return [_result_out(r) for r in rows]


@router.get("/domains/{domain_id}/dns/mta-sts-builder")
async def mta_sts_builder(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """Live DNS/HTTPS state + a safe starting policy for the MTA-STS policy
    builder UI — mirrors the DMARC Policy Builder's current_record/
    recommendation split, generated fresh each call (no DB storage, same
    as mta_sts.py's own check()). The mx: recommendation is deliberately
    an exact-match list of the real, currently-resolved MX hosts rather
    than a guessed wildcard — see _mx_covered's docstring for why a wrong
    wildcard is actively worse than none (the exact bug found against a
    real Microsoft 365 customer domain during development: mx: *.mx.microsoft
    looked plausible but didn't actually cover the real two-label MX host)."""
    domain = await _get_owned_domain(db, domain_id, user.organization_id)

    try:
        mx_records = await resolve_mx(domain.name)
    except DnsLookupError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not look up MX records: {exc}")

    mx_hosts: list[str] = []
    if mx_records and not is_null_mx(mx_records):
        for _preference, host in sorted(mx_records, key=lambda r: r[0]):
            if host not in mx_hosts:
                mx_hosts.append(host)

    current_txt: str | None = None
    try:
        txt_records = await resolve_txt_strict(f"_mta-sts.{domain.name}")
        sts_records = [r for r in txt_records if r.strip().lower().startswith("v=stsv1")]
        if sts_records:
            current_txt = sts_records[0].strip()
    except DnsLookupError:
        current_txt = None

    current_policy: dict | None = None
    current_policy_fetch_error: str | None = None
    fetch_result = await fetch_policy_file(domain.name)
    if fetch_result.error is not None:
        current_policy_fetch_error = fetch_result.error
    elif fetch_result.body is not None:
        parsed = _parse_policy(fetch_result.body)
        mx_patterns = parsed.get("mx", [])
        current_policy = {
            "raw": fetch_result.body,
            "mode": parsed.get("mode"),
            "mx_patterns": mx_patterns if isinstance(mx_patterns, list) else [],
            "max_age": parsed.get("max_age"),
        }

    recommended_mode = "testing"
    if current_policy and current_policy["mode"] == "enforce" and mx_hosts:
        if all(_mx_covered(h, current_policy["mx_patterns"]) for h in mx_hosts):
            recommended_mode = "enforce"

    return {
        "mx_hosts": mx_hosts,
        "current_txt": current_txt,
        "current_policy": current_policy,
        "current_policy_fetch_error": current_policy_fetch_error,
        "recommended_mode": recommended_mode,
    }


@router.get("/domains/{domain_id}/dns/tls-rpt-builder")
async def tls_rpt_builder(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """Live DNS state + report-destination status for the TLS-RPT policy
    builder UI — mirrors dmarc_rua_check/the DMARC Policy Builder's
    rua-destination check for _smtp._tls.<domain> instead of _dmarc.<domain>.
    Shares hosted_report_address with the DMARC side (see
    POST /domains/{id}/hosted-report-address) — one hosted mailbox per
    domain receives both DMARC aggregate and TLS-RPT reports, not two
    separate addresses."""
    domain = await _get_owned_domain(db, domain_id, user.organization_id)

    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == user.organization_id))
    ).scalar_one_or_none()
    mailbox_address = domain.hosted_report_address or (connection.mailbox_address if connection is not None else None)

    lookup_error = False
    try:
        current = await fetch_current_tls_rpt_record(domain.name)
    except DnsLookupError:
        current = None
        lookup_error = True

    if mailbox_address is not None:
        rua_result = await check_tls_rpt_rua_destination(domain.name, mailbox_address)
        rua_destination = {"status": rua_result.status, "current_targets": rua_result.current_targets}
    else:
        rua_destination = {"status": "no_mailbox", "current_targets": []}

    return {
        "current_record": {"raw": current.raw, "tags": current.tags} if current is not None else None,
        "current_record_lookup_error": lookup_error,
        "rua_destination": rua_destination,
        "org_mailbox_address": mailbox_address,
        "hosted_report_address": domain.hosted_report_address,
    }
