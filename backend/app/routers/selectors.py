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
from app.models.dmarc_aggregate import DmarcAggregateRecord
from app.models.domain import Domain
from app.models.user import User
from app.services.dmarc_narrative import describe_alignment

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


@router.get("/detected")
async def detected_selectors(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    """DKIM selectors this domain actually signed with, mined from already-
    ingested DMARC aggregate reports (auth_results.dkim, see
    dmarc_narrative.py's own confirmed-against-live-data shape note) rather
    than requiring the domain owner to dig one out of a raw email header —
    same "surface what the reports already show" idea as
    GET /dmarc/detected-domains. Only counts a (selector, dkim domain) pair
    if the DKIM d= aligns with this domain (see describe_alignment) — a
    passing DKIM signature from an unrelated third party (e.g. an ESP
    signing its own envelope) isn't this domain's selector to add."""
    domain = await _get_owned_domain(db, domain_id, user.organization_id)

    known = set(
        (await db.execute(select(DkimSelector.selector).where(DkimSelector.domain_id == domain_id))).scalars().all()
    )

    rows = (
        await db.execute(
            select(DmarcAggregateRecord.auth_results, DmarcAggregateRecord.report_id, DmarcAggregateRecord.count)
            .where(DmarcAggregateRecord.domain_id == domain_id)
        )
    ).all()

    stats: dict[str, dict] = {}
    for auth_results, report_id, count in rows:
        for entry in (auth_results or {}).get("dkim") or []:
            selector = entry.get("selector")
            dkim_domain = entry.get("domain")
            if not selector or selector in known:
                continue
            if describe_alignment(dkim_domain, domain.name) == "none":
                continue
            s = stats.setdefault(selector, {"report_ids": set(), "message_volume": 0})
            s["report_ids"].add(report_id)
            s["message_volume"] += count

    items = [
        {"selector": selector, "report_count": len(s["report_ids"]), "message_volume": s["message_volume"]}
        for selector, s in stats.items()
    ]
    items.sort(key=lambda x: -x["message_volume"])
    return items


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
