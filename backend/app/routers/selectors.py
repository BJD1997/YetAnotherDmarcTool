import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dkim_selector import DkimSelector
from app.models.user import User
from app.repositories.dmarc_reports import list_auth_results_for_domain
from app.repositories.domains import get_owned_domain
from app.repositories.selectors import known_selector_names, list_selectors_for_domain
from app.schemas.selectors import SelectorCreateRequest
from app.services.dmarc_narrative import describe_alignment

router = APIRouter(prefix="/domains/{domain_id}/selectors", tags=["dkim-selectors"])


def _selector_out(sel: DkimSelector) -> dict:
    return {
        "id": str(sel.id),
        "domain_id": str(sel.domain_id),
        "selector": sel.selector,
        "description": sel.description,
        "created_at": sel.created_at.isoformat(),
    }


@router.get("")
async def list_selectors(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> list[dict]:
    await get_owned_domain(db, domain_id, user.organization_id)
    selectors = await list_selectors_for_domain(db, domain_id)
    return [_selector_out(s) for s in selectors]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_selector(
    domain_id: uuid.UUID,
    body: SelectorCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    await get_owned_domain(db, domain_id, user.organization_id)

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
    domain = await get_owned_domain(db, domain_id, user.organization_id)
    known = await known_selector_names(db, domain_id)
    rows = await list_auth_results_for_domain(db, domain_id)

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
    await get_owned_domain(db, domain_id, user.organization_id)
    selector = await db.get(DkimSelector, selector_id)
    if selector is None or selector.domain_id != domain_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "selector not found")
    await db.delete(selector)
    await db.commit()
