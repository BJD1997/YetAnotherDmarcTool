"""The notifications behind the bell (see app/services/notifications).
Everyone in the organization reads them; org admins dismiss them. Links are
worked out on read, so a DKIM-selector notification points at its signing
sender until that sender is approved, then at the domain's selectors."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import SenderReviewStatus
from app.models.notification import Notification
from app.models.sender_review import SenderReview
from app.models.user import User
from app.repositories.notifications import get_notification, open_notifications, recently_resolved, resolve
from app.services.notifications.refresh import DOMAIN_DETECTED

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _approved_senders(db: AsyncSession, organization_id: uuid.UUID) -> set[tuple[uuid.UUID, str]]:
    rows = await db.execute(
        select(SenderReview.domain_id, SenderReview.service_label).where(
            SenderReview.organization_id == organization_id, SenderReview.status == SenderReviewStatus.approved
        )
    )
    return {(domain_id, label) for domain_id, label in rows.all()}


def _out(n: Notification, approved: set[tuple[uuid.UUID, str]]) -> dict:
    payload = n.payload or {}
    if n.kind == DOMAIN_DETECTED:
        volume = payload.get("message_volume") or 0
        title = f"New domain in your reports: {n.subject_key}"
        detail = f"{volume:,} messages · add it or dismiss it"
        link = "/settings/domains"
    else:
        label = payload.get("sender_label")
        title = f"{payload.get('domain_name')} signs with an unmonitored DKIM selector: {payload.get('selector')}"
        if label and (n.domain_id, label) not in approved:
            detail = f'Signed by "{label}", which isn\'t approved yet: review it first'
            link = f"/domains/{n.domain_id}/senders?highlight={quote(label, safe='')}"
        else:
            detail = "Add it so its key is checked"
            link = f"/domains/{n.domain_id}/dns?open=dkim-selectors"
    return {
        "id": str(n.id),
        "kind": n.kind,
        "title": title,
        "detail": detail,
        "link_path": link,
        "created_at": n.created_at.isoformat(),
        "resolved_at": n.resolved_at.isoformat() if n.resolved_at else None,
        "resolved_reason": n.resolved_reason,
    }


@router.get("")
async def list_notifications(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    """Open ones first (newest first), then the most recently resolved."""
    approved = await _approved_senders(db, user.organization_id)
    rows = [*await open_notifications(db, user.organization_id), *await recently_resolved(db, user.organization_id)]
    return [_out(n, approved) for n in rows]


@router.get("/count")
async def count_notifications(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return {"open": len(await open_notifications(db, user.organization_id))}


@router.post("/{notification_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_notification(
    notification_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> Response:
    notification = await get_notification(db, notification_id, user.organization_id)
    if notification is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    if notification.resolved_at is None:
        resolve(notification, "dismissed", user.id)
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
