import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import require_org_admin
from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent
from app.models.user import User
from app.repositories.sign_in_events import list_sign_in_events

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
async def list_sign_in_events_route(
    limit: int = Query(50, ge=1, le=200),
    before_id: uuid.UUID | None = Query(None),
    result: SignInResult | None = Query(None),
    auth_method: AuthMethod | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    events = await list_sign_in_events(
        db, user.organization_id, limit=limit, before_id=before_id, result=result, auth_method=auth_method
    )
    return {"events": [_event_out(e) for e in events], "has_more": len(events) == limit}
