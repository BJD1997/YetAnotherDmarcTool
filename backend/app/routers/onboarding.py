from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.user import User

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


async def _count(db: AsyncSession, model, *conditions) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*conditions))).scalar_one()


@router.get("/status")
async def onboarding_status(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Stateless setup-completeness snapshot, recomputed from existing data
    on every call — no separate "has this org finished onboarding" flag is
    stored anywhere, the same "no dismiss/acknowledge state" philosophy the
    action queue already uses. Drives both the onboarding wizard (which
    step to resume at) and Overview's onboarding-aware rendering."""
    org = await db.get(Organization, user.organization_id)

    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == user.organization_id))
    ).scalar_one_or_none()

    has_domain = await _count(db, Domain, Domain.organization_id == user.organization_id) > 0
    has_verified_domain = (
        await _count(
            db,
            Domain,
            Domain.organization_id == user.organization_id,
            Domain.verification_status == DomainVerificationStatus.verified,
        )
        > 0
    )
    has_dns_baseline = await _count(db, DnsCheckResult, DnsCheckResult.organization_id == user.organization_id) > 0
    has_any_report = (
        await _count(db, DmarcAggregateReport, DmarcAggregateReport.organization_id == user.organization_id) > 0
    )

    # A local-auth org (no entra_tenant_id) has no Entra tenant to grant
    # Mail Access consent from, so a MailboxConnection is never possible for
    # them — they get a hosted address per domain instead (see
    # app/routers/domains.py's _hosted_mailbox_available), which needs no
    # separate "connect a mailbox" step at all.
    has_mailbox = connection is not None or (org is not None and org.entra_tenant_id is None)

    return {
        "org_name": org.name if org is not None else None,
        "user_role": user.role.value,
        "has_mailbox": has_mailbox,
        "mailbox_consent_granted": connection is not None and connection.consent_status.value == "granted",
        "mailbox_last_sync_status": (
            connection.last_sync_status.value if connection is not None and connection.last_sync_status else None
        ),
        "has_domain": has_domain,
        "has_verified_domain": has_verified_domain,
        "has_dns_baseline": has_dns_baseline,
        "has_any_report": has_any_report,
    }
