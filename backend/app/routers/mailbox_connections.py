from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.user import User
from app.repositories.dmarc_reports import last_report_received_at_for_org
from app.repositories.mailbox_connections import get_org_mailbox_connection, list_mailbox_job_runs
from app.repositories.organizations import get_organization
from app.schemas.mailbox_connections import MailboxConnectionSetRequest
from app.workers.jobs.mailbox_poll_job import poll_org_mailbox

router = APIRouter(prefix="/mailbox-connection", tags=["mailbox-connection"])


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


async def _mailbox_health_extra(db: AsyncSession, organization_id) -> dict:
    """Fields that don't live on mailbox_connections itself: org-wide report
    freshness, and the message/report counters from the most recent sync
    attempt's job_runs.stats — a mailbox can report "success" while
    processing zero reports, which the bare consent/sync-status fields
    can't distinguish but this can."""
    last_report_at = await last_report_received_at_for_org(db, organization_id)

    job_runs = await list_mailbox_job_runs(db, organization_id, limit=1)
    last_run = job_runs[0] if job_runs else None

    return {
        "last_report_at": last_report_at.isoformat() if last_report_at else None,
        "last_run_stats": last_run.stats if last_run is not None else None,
    }


@router.get("")
async def get_mailbox_connection(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mailbox connection configured for your organization yet")
    extra = await _mailbox_health_extra(db, user.organization_id)
    return {**_connection_out(connection), **extra}


@router.get("/job-runs")
async def mailbox_job_runs(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    """Tenant-scoped sync history — job_runs already carries organization_id
    and RLS (see TENANT_SCOPED_TABLES in the initial migration), so a
    regular org member reading their own org's poll history needs nothing
    beyond the existing get_current_user context."""
    runs = await list_mailbox_job_runs(db, user.organization_id, limit=limit)
    return [
        {
            "id": str(r.id),
            "status": r.status.value,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "error_message": r.error_message,
            "stats": r.stats,
        }
        for r in runs
    ]


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
    org = await get_organization(db, user.organization_id)
    if org.entra_tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set yet — contact your platform administrator")

    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        connection = MailboxConnection(organization_id=user.organization_id, mailbox_address=body.mailbox_address)
        db.add(connection)
    elif connection.mailbox_address != body.mailbox_address:
        # delta_link is an opaque Graph URL bound to the *previous* mailbox's
        # /users/{mailbox}/... context — reusing it against a different
        # mailbox would either error or silently keep reading the old one.
        # Reset it so the next poll does a full fresh sync of the new
        # mailbox's existing backlog, same as a first-ever connection.
        connection.mailbox_address = body.mailbox_address
        connection.delta_link = None

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
    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mailbox connection configured for your organization yet")

    org = await get_organization(db, user.organization_id)
    if org.entra_tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set")

    background_tasks.add_task(poll_org_mailbox, organization_id=org.id, tenant_id=str(org.entra_tenant_id))
    return {"status": "resync started"}
