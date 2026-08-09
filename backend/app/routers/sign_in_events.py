import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import require_org_admin
from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent
from app.models.user import User

router = APIRouter(prefix="/sign-in-events", tags=["sign-in-events"])


def _event_out(event: SignInEvent) -> dict:
    return {
        "id": str(event.id),
        "created_at": event.created_at.isoformat(),
        "result": event.result.value,
        "auth_method": event.auth_method.value,
        "email": event.attempted_email,
        "failure_reason": event.failure_reason,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
    }


@router.get("")
async def list_sign_in_events(
    limit: int = Query(50, ge=1, le=200),
    before_id: uuid.UUID | None = Query(None),
    result: SignInResult | None = Query(None),
    auth_method: AuthMethod | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    """Org-admin only — this surfaces every member's attempted emails/IPs,
    not just the viewer's own, same admin-only bar Settings.tsx already
    applies to its other sections. Keyset-paginated on (created_at, id),
    same shape as dmarc_reports_by_day, rather than OFFSET — this table only
    grows, and an admin scrolling through pages shouldn't see rows shift
    around as new sign-ins land between requests."""
    query = select(SignInEvent).where(SignInEvent.organization_id == user.organization_id)
    if result is not None:
        query = query.where(SignInEvent.result == result)
    if auth_method is not None:
        query = query.where(SignInEvent.auth_method == auth_method)

    if before_id is not None:
        anchor = (
            await db.execute(
                select(SignInEvent.created_at, SignInEvent.id).where(
                    SignInEvent.id == before_id, SignInEvent.organization_id == user.organization_id
                )
            )
        ).first()
        if anchor is not None:
            query = query.where(tuple_(SignInEvent.created_at, SignInEvent.id) < anchor)

    query = query.order_by(SignInEvent.created_at.desc(), SignInEvent.id.desc()).limit(limit)
    rows = (await db.execute(query)).scalars().all()
    return {"events": [_event_out(e) for e in rows], "has_more": len(rows) == limit}
