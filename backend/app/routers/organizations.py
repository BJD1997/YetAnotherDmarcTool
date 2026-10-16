from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.organization import Organization
from app.models.trusted_reporter import TrustedReporter
from app.models.user import User
from app.repositories.dmarc_reports import apply_report_sender_check, trust_reporter, untrust_reporter
from app.repositories.organizations import get_organization
from app.schemas.organizations import OrganizationUpdateRequest, TrustedReporterRequest
from app.services.auth.entra_links import entra_consent_urls

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _org_out(org: Organization) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "status": org.status.value,
        "entra_tenant_id": str(org.entra_tenant_id) if org.entra_tenant_id else None,
        "sign_in_provider": org.sign_in_provider,
        "is_operator": org.is_operator,
        "spf_all_qualifier_mode": org.spf_all_qualifier_mode.value,
        "hosted_mailbox_opt_in": org.hosted_mailbox_opt_in,
        "report_sender_check": org.report_sender_check.value,
        "rating_window_days": org.rating_window_days,
        "ask_ai_enabled": org.ask_ai_enabled,
        "is_demo_read_only": org.is_demo_read_only,
        # Self-service reference: e.g. if the SSO consent link is needed
        # later, or the Mail Access consent needs redoing after a lapse.
        "entra_consent_urls": entra_consent_urls(org),
    }


@router.get("/current")
async def get_current_organization(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    org = await get_organization(db, user.organization_id)
    return _org_out(org)


@router.patch("/current")
async def update_current_organization(
    body: OrganizationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    org = await get_organization(db, user.organization_id)
    org.name = body.name
    if body.spf_all_qualifier_mode is not None:
        org.spf_all_qualifier_mode = body.spf_all_qualifier_mode
    if body.hosted_mailbox_opt_in is not None:
        org.hosted_mailbox_opt_in = body.hosted_mailbox_opt_in
    if body.ask_ai_enabled is not None:
        org.ask_ai_enabled = body.ask_ai_enabled
    if body.rating_window_days is not None:
        org.rating_window_days = body.rating_window_days
    if body.report_sender_check is not None and body.report_sender_check != org.report_sender_check:
        org.report_sender_check = body.report_sender_check
        # Applies to reports already received, not just new ones.
        await apply_report_sender_check(db, org.id, body.report_sender_check)
    await db.commit()
    await db.refresh(org)
    return _org_out(org)


async def _trusted_reporters_out(db: AsyncSession, organization_id) -> list[dict]:
    rows = await db.execute(
        select(TrustedReporter).where(TrustedReporter.organization_id == organization_id).order_by(TrustedReporter.domain)
    )
    return [{"domain": r.domain, "created_at": r.created_at.isoformat()} for r in rows.scalars()]


@router.get("/current/trusted-reporters")
async def list_trusted_reporters(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    """Report senders the organization has chosen to believe, on top of the
    built-in known reporters (sender_auth.KNOWN_REPORTERS)."""
    return await _trusted_reporters_out(db, user.organization_id)


@router.post("/current/trusted-reporters")
async def add_trusted_reporter(
    body: TrustedReporterRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> list[dict]:
    org = await get_organization(db, user.organization_id)
    await trust_reporter(db, org.id, body.domain, user.id)
    # Counts its reports already received too, not just new ones.
    await apply_report_sender_check(db, org.id, org.report_sender_check)
    out = await _trusted_reporters_out(db, org.id)
    await db.commit()
    return out


@router.delete("/current/trusted-reporters/{domain}")
async def remove_trusted_reporter(
    domain: str, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> list[dict]:
    org = await get_organization(db, user.organization_id)
    if not await untrust_reporter(db, org.id, domain.strip().lower().rstrip(".")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not a trusted reporter")
    await apply_report_sender_check(db, org.id, org.report_sender_check)
    out = await _trusted_reporters_out(db, org.id)
    await db.commit()
    return out
