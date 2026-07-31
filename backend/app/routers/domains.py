import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.models.user import User
from app.services.dns_checks.domain_verification import verification_record_name, verify_domain_ownership
from app.services.ingestion.report_writer import resweep_unmatched_reports

router = APIRouter(prefix="/domains", tags=["domains"])

_DOMAIN_NAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class DomainCreateRequest(BaseModel):
    name: str
    parent_domain_id: uuid.UUID | None = None
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not _DOMAIN_NAME_RE.match(value):
            raise ValueError("not a valid domain name")
        return value


class DomainUpdateRequest(BaseModel):
    notes: str | None = None
    is_active: bool | None = None


def _domain_out(domain: Domain) -> dict:
    return {
        "id": str(domain.id),
        "name": domain.name,
        "parent_domain_id": str(domain.parent_domain_id) if domain.parent_domain_id else None,
        "notes": domain.notes,
        "is_active": domain.is_active,
        "created_at": domain.created_at.isoformat(),
        "verification_status": domain.verification_status.value,
        "verified_at": domain.verified_at.isoformat() if domain.verified_at else None,
        # Only meaningful while pending, but harmless (and convenient for the
        # UI) to include once verified too, rather than making callers guess.
        "verification_token": domain.verification_token,
        "verification_record_name": verification_record_name(domain.name),
    }


@router.get("")
async def list_domains(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    result = await db.execute(
        select(Domain).where(Domain.organization_id == user.organization_id).order_by(Domain.name)
    )
    return [_domain_out(d) for d in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_domain(
    body: DomainCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    parent: Domain | None = None
    if body.parent_domain_id is not None:
        parent = await db.get(Domain, body.parent_domain_id)
        if parent is None or parent.organization_id != user.organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_domain_id not found in your organization")
        if parent.parent_domain_id is not None:
            # Only one level of nesting: apex domain -> subdomains. Prevents
            # building arbitrarily deep/cyclic trees the UI isn't designed for.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "parent_domain_id must itself be an apex domain, not a subdomain"
            )
        if not body.name.endswith(f".{parent.name}"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{body.name!r} is not a subdomain of {parent.name!r}"
            )

    domain = Domain(
        organization_id=user.organization_id,
        parent_domain_id=body.parent_domain_id,
        name=body.name,
        notes=body.notes,
    )
    if parent is not None and parent.verification_status == DomainVerificationStatus.verified:
        # Proving control of the apex zone's DNS implies control of
        # everything under it — a subdomain of an already-verified apex
        # doesn't need its own separate TXT challenge.
        domain.verification_status = DomainVerificationStatus.verified
        domain.verified_at = datetime.now(timezone.utc)

    db.add(domain)
    try:
        # flush (not commit) so the row is visible to the refresh() SELECT
        # below within the SAME transaction — the RLS context set by
        # require_org_admin/get_current_user via SET LOCAL only lasts for
        # that one transaction, so a commit here would make a subsequent
        # refresh() run in a new, unscoped transaction and find nothing.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "this domain is already registered")

    # Reports that arrived before this domain was registered are stuck with
    # domain_id=NULL in the unmatched bucket — re-check them now that a
    # domain exists to match against, rather than leaving them stranded
    # until the next unrelated write happens to touch them.
    resweep_counts = await resweep_unmatched_reports(db, user.organization_id)

    await db.refresh(domain)
    await db.commit()
    return {**_domain_out(domain), "reattributed_reports": resweep_counts["aggregate_reports"]}


@router.get("/{domain_id}")
async def get_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return _domain_out(domain)


@router.post("/{domain_id}/verify")
async def verify_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")

    if domain.verification_status == DomainVerificationStatus.verified:
        return {"verified": True, "domain": _domain_out(domain)}

    ok = await verify_domain_ownership(domain.name, domain.verification_token)
    if not ok:
        return {"verified": False, "domain": _domain_out(domain)}

    now = datetime.now(timezone.utc)
    domain.verification_status = DomainVerificationStatus.verified
    domain.verified_at = now

    if domain.parent_domain_id is None:
        # Propagate to subdomains added before this apex got verified (they
        # started pending, per create_domain, since there was nothing yet
        # to inherit from). Done in the SAME transaction as the apex update
        # above, both committed together below — see the flush/refresh/commit
        # ordering note on create_domain: a commit here before this second
        # write would drop the RLS org context this bulk UPDATE needs.
        await db.execute(
            Domain.__table__.update()
            .where(
                Domain.parent_domain_id == domain.id,
                Domain.verification_status == DomainVerificationStatus.pending,
            )
            .values(verification_status=DomainVerificationStatus.verified, verified_at=now)
        )

    await db.flush()
    await db.refresh(domain)
    await db.commit()
    return {"verified": True, "domain": _domain_out(domain)}


@router.patch("/{domain_id}")
async def update_domain(
    domain_id: uuid.UUID,
    body: DomainUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    if body.notes is not None:
        domain.notes = body.notes
    if body.is_active is not None:
        domain.is_active = body.is_active
    await db.flush()
    await db.refresh(domain)
    await db.commit()
    return _domain_out(domain)


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> None:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")

    subdomain_count = await db.execute(
        select(func.count()).select_from(Domain).where(Domain.parent_domain_id == domain_id)
    )
    if subdomain_count.scalar_one() > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "remove or reassign subdomains first")

    report_count = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.domain_id == domain_id)
    )
    if report_count.scalar_one() > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "domain has report history — archive it instead (PATCH is_active=false)"
        )

    await db.delete(domain)
    await db.commit()
