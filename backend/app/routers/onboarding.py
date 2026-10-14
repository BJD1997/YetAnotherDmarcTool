from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.repositories.dmarc_reports import count_reports_for_org
from app.repositories.dns_checks import count_dns_checks_for_org
from app.repositories.domains import count_domains_for_org, count_verified_domains_for_org
from app.repositories.mailbox_connections import get_org_mailbox_connection
from app.repositories.organizations import get_organization
from app.models.user import User

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/status")
async def onboarding_status(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """Setup-completeness snapshot, recomputed from existing data on every
    call, plus whether an org admin finished the wizard. Drives both the
    onboarding wizard (which step to resume at) and Overview's
    onboarding-aware rendering."""
    org = await get_organization(db, user.organization_id)
    connection = await get_org_mailbox_connection(db, user.organization_id)

    has_domain = await count_domains_for_org(db, user.organization_id) > 0
    has_verified_domain = await count_verified_domains_for_org(db, user.organization_id) > 0
    has_dns_baseline = await count_dns_checks_for_org(db, user.organization_id) > 0
    has_any_report = await count_reports_for_org(db, user.organization_id) > 0

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
        # Organizations without Microsoft sign-in can only receive reports
        # through the server's hosted mailbox: the wizard says so plainly
        # when this server doesn't have one.
        "hosted_mailbox_ready": settings.hosted_mailbox_ready,
        "wizard_completed": org is not None and org.onboarding_completed_at is not None,
    }


@router.post("/complete")
async def complete_onboarding(db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)) -> dict:
    """An org admin reached the wizard's last step. Idempotent."""
    org = await get_organization(db, user.organization_id)
    if org.onboarding_completed_at is None:
        org.onboarding_completed_at = datetime.now(timezone.utc)
        await db.commit()
    return {"wizard_completed": True}
