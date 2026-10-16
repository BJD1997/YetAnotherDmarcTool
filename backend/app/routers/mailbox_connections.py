from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import ConsentStatus, MailboxKind
from app.models.mailbox_connection import MailboxConnection
from app.models.user import User
from app.repositories.dmarc_reports import last_report_received_at_for_org
from app.repositories.mailbox_connections import get_org_mailbox_connection, list_mailbox_job_runs
from app.repositories.organizations import get_organization
from app.schemas.mailbox_connections import ImapConnectionRequest, MailboxConnectionSetRequest
from app.services.imap.client import ImapError, ImapSettings
from app.services.imap.poll import test_connection
from app.services.jobs import queue

router = APIRouter(prefix="/mailbox-connection", tags=["mailbox-connection"])


def _connection_out(connection: MailboxConnection) -> dict:
    imap = None
    if connection.kind == MailboxKind.imap:
        imap = {
            "host": connection.imap_host,
            "port": connection.imap_port,
            "security": connection.imap_security.value if connection.imap_security else None,
            "username": connection.imap_username,
            "report_address": connection.mailbox_address,
            "folder": connection.imap_folder,
            "processed_folder": connection.imap_processed_folder,
            "authserv_id": connection.imap_authserv_id,
            "detected_authserv_id": connection.imap_detected_authserv_id,
            # The password itself is never sent back.
            "password_set": bool(connection.imap_password),
        }
    return {
        "id": str(connection.id),
        "kind": connection.kind.value,
        "imap": imap,
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


async def _queue_sync(db: AsyncSession, organization_id, tenant_id=None) -> None:
    """Queues a sync for the worker rather than running it here: the worker
    holds the Graph app's secret, and on Azure the api doesn't (the sync
    failed there with AADSTS7000216). Same job and dedupe key the worker's
    10-minute schedule uses, so a sync already waiting isn't doubled."""
    await queue.enqueue(
        db,
        "mailbox_poll",
        {"org_id": str(organization_id), "tenant_id": str(tenant_id) if tenant_id else None},
        dedupe_key=f"mailbox_poll:{organization_id}",
    )


@router.put("", status_code=status.HTTP_200_OK)
async def set_mailbox_connection(
    body: MailboxConnectionSetRequest,
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
    silently pretending to have succeeded. The sync itself runs on the
    worker (see _queue_sync)."""
    org = await get_organization(db, user.organization_id)
    connection = await get_org_mailbox_connection(db, user.organization_id)
    # The mailbox's own tenant: the organization's Microsoft sign-in tenant,
    # or the one this mailbox already had (the organization may have moved
    # its sign-in elsewhere since).
    tenant_id = org.entra_tenant_id or (
        connection.ms_tenant_id if connection is not None and connection.kind == MailboxKind.microsoft else None
    )
    if tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set yet — contact your platform administrator")

    if connection is None:
        connection = MailboxConnection(organization_id=user.organization_id, mailbox_address=body.mailbox_address)
        db.add(connection)
    elif connection.kind != MailboxKind.microsoft:
        # One mailbox per organization: switching replaces the IMAP one.
        _clear_imap(connection)
        connection.mailbox_address = body.mailbox_address
        connection.delta_link = None
    elif connection.mailbox_address != body.mailbox_address:
        # delta_link is an opaque Graph URL bound to the *previous* mailbox's
        # /users/{mailbox}/... context — reusing it against a different
        # mailbox would either error or silently keep reading the old one.
        # Reset it so the next poll does a full fresh sync of the new
        # mailbox's existing backlog, same as a first-ever connection.
        connection.mailbox_address = body.mailbox_address
        connection.delta_link = None

    connection.ms_tenant_id = tenant_id
    connection.consent_status = ConsentStatus.granted
    connection.consent_granted_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(connection)
    await db.commit()

    await _queue_sync(db, org.id, tenant_id)

    return _connection_out(connection)


@router.post("/resync", status_code=status.HTTP_202_ACCEPTED)
async def resync_mailbox_connection(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no mailbox connection configured for your organization yet")

    if connection.kind == MailboxKind.microsoft and connection.ms_tenant_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "organization has no Entra tenant ID set")

    await _queue_sync(db, user.organization_id, connection.ms_tenant_id)
    return {"status": "resync started"}


def _clear_imap(connection: MailboxConnection) -> None:
    connection.kind = MailboxKind.microsoft
    for field in (
        "imap_host", "imap_port", "imap_security", "imap_username", "imap_password", "imap_folder",
        "imap_processed_folder", "imap_authserv_id", "imap_detected_authserv_id",
    ):
        setattr(connection, field, None)


async def _imap_settings(db: AsyncSession, organization_id, body: ImapConnectionRequest) -> ImapSettings:
    """The settings to test or save. Without a new password, the stored one
    is reused, but only for the same server and username: otherwise a
    changed server could be sent the old password."""
    password = body.password
    if password is None:
        current = await get_org_mailbox_connection(db, organization_id)
        same = (
            current is not None
            and current.kind == MailboxKind.imap
            and current.imap_host == body.host
            and current.imap_username == body.username
        )
        if not same or not current.imap_password:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "enter the password")
        password = current.imap_password
    return ImapSettings(
        host=body.host, port=body.port, security=body.security, username=body.username, password=password,
        folder=body.folder, processed_folder=body.processed_folder,
    )


async def _tested(settings: ImapSettings) -> tuple[int, str | None]:
    try:
        return await test_connection(settings)
    except ImapError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Couldn't use this mailbox: {exc}") from exc


@router.post("/imap/test")
async def test_imap_connection(
    body: ImapConnectionRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    """Connects, signs in and opens the folder without changing anything."""
    messages, detected = await _tested(await _imap_settings(db, user.organization_id, body))
    return {"ok": True, "messages": messages, "detected_authserv_id": detected}


@router.put("/imap")
async def set_imap_connection(
    body: ImapConnectionRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    """Saves an IMAP mailbox as the organization's one mailbox, replacing a
    Microsoft 365 one. Only after a successful connection test, so a typo
    doesn't leave the organization with a mailbox that never syncs."""
    mailbox_address = body.mailbox_address
    if mailbox_address is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "enter the address reports are sent to: the username isn't an email address"
        )
    settings = await _imap_settings(db, user.organization_id, body)
    _messages, detected = await _tested(settings)

    connection = await get_org_mailbox_connection(db, user.organization_id)
    if connection is None:
        connection = MailboxConnection(organization_id=user.organization_id, mailbox_address=mailbox_address)
        db.add(connection)
    connection.kind = MailboxKind.imap
    connection.mailbox_address = mailbox_address
    connection.imap_host = settings.host
    connection.imap_port = settings.port
    connection.imap_security = settings.security
    connection.imap_username = settings.username
    connection.imap_password = settings.password
    connection.imap_folder = settings.folder
    connection.imap_processed_folder = settings.processed_folder
    connection.imap_authserv_id = body.authserv_id
    connection.imap_detected_authserv_id = detected
    connection.delta_link = None
    connection.consent_status = ConsentStatus.granted
    connection.consent_granted_at = datetime.now(timezone.utc)
    connection.last_sync_status = None
    connection.last_sync_error = None

    await db.flush()
    await db.refresh(connection)
    await db.commit()
    await _queue_sync(db, user.organization_id)
    return _connection_out(connection)
