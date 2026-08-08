from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import SpfAllQualifierMode
from app.models.organization import Organization
from app.models.user import User
from app.services.auth.entra_links import entra_consent_urls

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationUpdateRequest(BaseModel):
    name: str
    spf_all_qualifier_mode: SpfAllQualifierMode | None = None
    hosted_mailbox_opt_in: bool | None = None


def _org_out(org: Organization) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "status": org.status.value,
        "entra_tenant_id": str(org.entra_tenant_id) if org.entra_tenant_id else None,
        "is_operator": org.is_operator,
        "spf_all_qualifier_mode": org.spf_all_qualifier_mode.value,
        "hosted_mailbox_opt_in": org.hosted_mailbox_opt_in,
        # Self-service reference: e.g. if the SSO consent link is needed
        # later, or the Mail Access consent needs redoing after a lapse.
        "entra_consent_urls": entra_consent_urls(org),
    }


@router.get("/current")
async def get_current_organization(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    org = await db.get(Organization, user.organization_id)
    return _org_out(org)


@router.patch("/current")
async def update_current_organization(
    body: OrganizationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    org = await db.get(Organization, user.organization_id)
    org.name = body.name
    if body.spf_all_qualifier_mode is not None:
        org.spf_all_qualifier_mode = body.spf_all_qualifier_mode
    if body.hosted_mailbox_opt_in is not None:
        org.hosted_mailbox_opt_in = body.hosted_mailbox_opt_in
    await db.commit()
    await db.refresh(org)
    return _org_out(org)
