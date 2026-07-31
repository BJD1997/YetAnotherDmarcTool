from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.user import User
from app.workers.jobs.mailbox_poll_job import poll_org_mailbox

router = APIRouter(prefix="/mailbox-connection", tags=["mailbox-connection"])


class MailboxConnectionSetRequest(BaseModel):
    mailbox_address: str


def _connection_out(connection: MailboxConnection) -> dict:
    return {
        "id": str(connection.id),
        "mailbox_address": connection.mailbox_address,
        "consent_status": connection.consent_status.value,
        "consent_granted_at": connection.consent_granted_at.isoformat() if connection.consent_granted_at else None,
        "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        "last_sync_status": connection.last_sync_status.value if connection.last_sync_status else None,
        "last_sync_error": connection.last_sync_error,
    }


async def _get_org_connection(db: AsyncSession, organization_id) -> MailboxConnection | None:
    result = await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == organization_id))
    return result.scalar_one_or_none()


@router.get("")
async def get_mailbox_connection(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    connection = await _get_org_connection(db, user.organization_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mailbox connection configured for your organization yet")
    return _connection_out(connection)


@router.put("", status_code=status.HTTP_200_OK)
async def set_mailbox_connection(
    body: MailboxConnectionSetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    """Self-service: an org_admin sets their own shared mailbox address —
    the platform admin no longer needs to touch this. Setting it is treated
    as self-attested confirmation that the Entra admin-consent steps (see
    /organizations/current's entra_consent_urls) and the Exchange
    Application Access Policy are done — we don't (can't, from here) verify
    that independently, so consent_status flips to "granted" immediately
    and a resync is kicked off right away. If the Entra/Exchange side
    genuinely isn't done yet, that resync will simply fail with a clear
    error surfaced via last_sync_status/last_sync_error, rather than
    silently pretending to have succeeded."""
    org = await db.get(Organization, user.organization_id)
    if org.entra_tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set yet — contact your platform administrator")

    connection = await _get_org_connection(db, user.organization_id)
    if connection is None:
        connection = MailboxConnection(organization_id=user.organization_id, mailbox_address=body.mailbox_address)
        db.add(connection)
    else:
        connection.mailbox_address = body.mailbox_address

    connection.consent_status = ConsentStatus.granted
    connection.consent_granted_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(connection)
    await db.commit()

    background_tasks.add_task(poll_org_mailbox, organization_id=org.id, tenant_id=str(org.entra_tenant_id))

    return _connection_out(connection)


@router.post("/resync", status_code=status.HTTP_202_ACCEPTED)
async def resync_mailbox_connection(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    connection = await _get_org_connection(db, user.organization_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mailbox connection configured for your organization yet")

    org = await db.get(Organization, user.organization_id)
    if org.entra_tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set")

    background_tasks.add_task(poll_org_mailbox, organization_id=org.id, tenant_id=str(org.entra_tenant_id))
    return {"status": "resync started"}
