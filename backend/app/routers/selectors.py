import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dkim_selector import DkimSelector
from app.models.domain import Domain
from app.models.user import User

router = APIRouter(prefix="/domains/{domain_id}/selectors", tags=["dkim-selectors"])

# DKIM selectors are a DNS label sequence (RFC 6376 "selector"); allow the
# usual hostname-label characters plus dots for multi-label selectors.
_SELECTOR_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?(\.[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?)*$")


class SelectorCreateRequest(BaseModel):
    selector: str
    description: str | None = None

    @field_validator("selector")
    @classmethod
    def validate_selector(cls, value: str) -> str:
        value = value.strip()
        if not _SELECTOR_RE.match(value):
            raise ValueError("not a valid DKIM selector")
        return value


def _selector_out(sel: DkimSelector) -> dict:
    return {
        "id": str(sel.id),
        "domain_id": str(sel.domain_id),
        "selector": sel.selector,
        "description": sel.description,
        "created_at": sel.created_at.isoformat(),
    }


async def _get_owned_domain(db: AsyncSession, domain_id: uuid.UUID, organization_id: uuid.UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return domain


@router.get("")
async def list_selectors(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    await _get_owned_domain(db, domain_id, user.organization_id)
    result = await db.execute(
        select(DkimSelector).where(DkimSelector.domain_id == domain_id).order_by(DkimSelector.selector)
    )
    return [_selector_out(s) for s in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_selector(
    domain_id: uuid.UUID,
    body: SelectorCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    await _get_owned_domain(db, domain_id, user.organization_id)

    selector = DkimSelector(
        organization_id=user.organization_id,
        domain_id=domain_id,
        selector=body.selector,
        description=body.description,
    )
    db.add(selector)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "this selector is already registered for this domain")
    await db.refresh(selector)
    await db.commit()
    return _selector_out(selector)


@router.delete("/{selector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_selector(
    domain_id: uuid.UUID,
    selector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> None:
    await _get_owned_domain(db, domain_id, user.organization_id)
    selector = await db.get(DkimSelector, selector_id)
    if selector is None or selector.domain_id != domain_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "selector not found")
    await db.delete(selector)
    await db.commit()
