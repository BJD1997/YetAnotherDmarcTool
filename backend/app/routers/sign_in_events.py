from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import require_org_admin
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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> list[dict]:
    """Org-admin only — this surfaces every member's attempted emails/IPs,
    not just the viewer's own, same admin-only bar Settings.tsx already
    applies to its other sections."""
    result = await db.execute(
        select(SignInEvent)
        .where(SignInEvent.organization_id == user.organization_id)
        .order_by(SignInEvent.created_at.desc())
        .limit(limit)
    )
    return [_event_out(e) for e in result.scalars().all()]
