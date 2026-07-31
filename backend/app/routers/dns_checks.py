import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dkim_selector import DkimSelector
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import CheckStatus, CheckType, DomainVerificationStatus
from app.models.user import User
from app.services.dns_checks.registry import run_all

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


@router.post("/domains/{domain_id}/checks/recheck")
async def recheck_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> list[dict]:
    domain = await _get_owned_domain(db, domain_id, user.organization_id)

    if domain.verification_status != DomainVerificationStatus.verified:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "domain must be verified before best-practice checks can run"
        )

    selector_result = await db.execute(select(DkimSelector).where(DkimSelector.domain_id == domain_id))
    selectors = selector_result.scalars().all()
    selector_id_by_name = {s.selector: s.id for s in selectors}

    findings_by_type = await run_all(domain.name, [s.selector for s in selectors])

    now = datetime.now(timezone.utc)
    rows = [
        DnsCheckResult(
            organization_id=user.organization_id,
            domain_id=domain_id,
            check_type=check_type,
            dkim_selector_id=selector_id_by_name.get(finding.subject) if check_type == CheckType.dkim else None,
            subject=finding.subject,
            status=CheckStatus(finding.status),
            summary=finding.summary,
            details=finding.details,
            rule_version=finding.rule_version,
            checked_at=now,
        )
        for check_type, findings in findings_by_type.items()
        for finding in findings
    ]

    db.add_all(rows)
    await db.flush()
    await db.commit()

    return [_result_out(r) for r in rows]
