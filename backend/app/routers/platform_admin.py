import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import (
    AdminPrincipal,
    get_current_platform_admin,
    get_current_platform_admin_local,
)
from app.models.enums import ConsentStatus, OrganizationStatus
from app.models.job_run import JobRun
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.platform_admin import PlatformAdmin
from app.services.auth import session_manager
from app.services.auth.entra_links import entra_consent_urls
from app.services.auth.password import hash_password, verify_password
from app.services.auth.session_manager import cookie_kwargs

router = APIRouter(prefix="/admin", tags=["platform-admin"])


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str


class OrganizationCreateRequest(BaseModel):
    name: str
    entra_tenant_id: uuid.UUID | None = None


class OrganizationUpdateRequest(BaseModel):
    name: str | None = None
    entra_tenant_id: uuid.UUID | None = None
    status: OrganizationStatus | None = None


class MailboxConnectionRequest(BaseModel):
    mailbox_address: str
    consent_status: ConsentStatus | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if len(value) < 12:
            raise ValueError("new password must be at least 12 characters")
        return value


async def _mailbox_out(db: AsyncSession, org_id: uuid.UUID) -> dict | None:
    result = await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org_id))
    connection = result.scalar_one_or_none()
    if connection is None:
        return None
    return {
        "id": str(connection.id),
        "mailbox_address": connection.mailbox_address,
        "consent_status": connection.consent_status.value,
        "consent_granted_at": connection.consent_granted_at.isoformat() if connection.consent_granted_at else None,
        "last_sync_at": connection.last_sync_at.isoformat() if connection.last_sync_at else None,
        "last_sync_status": connection.last_sync_status.value if connection.last_sync_status else None,
        "last_sync_error": connection.last_sync_error,
    }


async def _org_out(db: AsyncSession, org: Organization) -> dict:
    return {
        "id": str(org.id),
        "name": org.name,
        "entra_tenant_id": str(org.entra_tenant_id) if org.entra_tenant_id else None,
        "status": org.status.value,
        "is_operator": org.is_operator,
        "created_at": org.created_at.isoformat(),
        "mailbox_connection": await _mailbox_out(db, org.id),
        "entra_consent_urls": entra_consent_urls(org),
    }


@router.post("/login")
async def admin_login(body: AdminLoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    result = await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == body.email))
    admin = result.scalar_one_or_none()
    if admin is None or not admin.is_active or not verify_password(body.password, admin.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    _, raw_token = await session_manager.create_platform_admin_session(
        db,
        platform_admin_id=admin.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    response = Response(status_code=204)
    response.set_cookie(
        settings.platform_admin_session_cookie_name,
        raw_token,
        max_age=settings.session_idle_timeout_hours * 3600,
        **cookie_kwargs(),
    )
    return response


@router.post("/logout")
async def admin_logout(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    raw_token = request.cookies.get(settings.platform_admin_session_cookie_name)
    if raw_token:
        session = await session_manager.get_active_platform_admin_session(db, raw_token)
        if session is not None:
            session_manager.revoke_session(session)
            await db.commit()
    response = Response(status_code=204)
    response.delete_cookie(settings.platform_admin_session_cookie_name, path="/")
    return response


@router.get("/me")
async def admin_me(admin: AdminPrincipal = Depends(get_current_platform_admin)) -> dict:
    return {"id": str(admin.id), "email": admin.email, "auth_type": admin.auth_type}


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    admin: PlatformAdmin = Depends(get_current_platform_admin_local),
) -> None:
    if not verify_password(body.current_password, admin.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "current password is incorrect")
    admin.password_hash = hash_password(body.new_password)
    await db.commit()


@router.get("/organizations")
async def list_organizations(
    db: AsyncSession = Depends(get_db), _admin: AdminPrincipal = Depends(get_current_platform_admin)
) -> list[dict]:
    result = await db.execute(select(Organization).order_by(Organization.created_at.desc()))
    return [await _org_out(db, org) for org in result.scalars().all()]


@router.post("/organizations", status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrganizationCreateRequest,
    db: AsyncSession = Depends(get_db),
    _admin: AdminPrincipal = Depends(get_current_platform_admin),
) -> dict:
    org = Organization(name=body.name, entra_tenant_id=body.entra_tenant_id, status=OrganizationStatus.active)
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return await _org_out(db, org)


@router.get("/organizations/{org_id}")
async def get_organization(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: AdminPrincipal = Depends(get_current_platform_admin),
) -> dict:
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
    return await _org_out(db, org)


@router.patch("/organizations/{org_id}")
async def update_organization(
    org_id: uuid.UUID,
    body: OrganizationUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _admin: AdminPrincipal = Depends(get_current_platform_admin),
) -> dict:
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
    if body.name is not None:
        org.name = body.name
    if body.entra_tenant_id is not None:
        org.entra_tenant_id = body.entra_tenant_id
    if body.status is not None:
        org.status = body.status
    await db.commit()
    await db.refresh(org)
    return await _org_out(db, org)


@router.delete("/organizations/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: AdminPrincipal = Depends(get_current_platform_admin),
) -> None:
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
    if org.is_operator:
        # The operator org is what lets its own org_admins reach /admin/*
        # without the local break-glass login — deleting it out from under
        # them isn't something to allow by accident. Unset is_operator on
        # another org first if the operator designation needs to move.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot delete the operator organization")

    # No ORM-level cascade is configured (Organization has no relationship()s
    # — see the model), but every child table's FK to organizations.id is
    # ondelete=CASCADE at the DB level, so this one delete removes the org's
    # entire history: mailbox connection, domains, DKIM selectors, DNS check
    # results, DMARC/TLS-RPT/forensic reports, users, sessions, job runs.
    # Postgres enforces each child table's RLS policy during the cascade,
    # not just on the top-level DELETE — get_current_platform_admin already
    # set app.is_platform_admin=true on this transaction (same as every
    # other route in this file), which is what lets the cascade clear every
    # child table's policy regardless of organization_id, not an org-scoped
    # bypass that would need repeating here.
    # Irreversible by design — the frontend requires typing the org's name
    # to confirm before this endpoint is ever called.
    await db.delete(org)
    await db.commit()


@router.post("/organizations/{org_id}/mailbox-connection", status_code=status.HTTP_201_CREATED)
async def upsert_mailbox_connection(
    org_id: uuid.UUID,
    body: MailboxConnectionRequest,
    db: AsyncSession = Depends(get_db),
    _admin: AdminPrincipal = Depends(get_current_platform_admin),
) -> dict:
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")

    result = await db.execute(
        select(MailboxConnection).where(MailboxConnection.organization_id == org_id)
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = MailboxConnection(organization_id=org_id, mailbox_address=body.mailbox_address)
        db.add(connection)
    else:
        connection.mailbox_address = body.mailbox_address

    if body.consent_status is not None and body.consent_status != connection.consent_status:
        connection.consent_status = body.consent_status
        if body.consent_status == ConsentStatus.granted:
            connection.consent_granted_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(connection)
    await db.commit()
    return {
        "id": str(connection.id),
        "organization_id": str(connection.organization_id),
        "mailbox_address": connection.mailbox_address,
        "consent_status": connection.consent_status.value,
        "consent_granted_at": connection.consent_granted_at.isoformat() if connection.consent_granted_at else None,
    }


@router.get("/job-runs")
async def list_job_runs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: AdminPrincipal = Depends(get_current_platform_admin),
) -> list[dict]:
    limit = max(1, min(limit, 200))
    result = await db.execute(select(JobRun).order_by(JobRun.started_at.desc()).limit(limit))
    return [
        {
            "id": str(run.id),
            "job_type": run.job_type.value,
            "organization_id": str(run.organization_id) if run.organization_id else None,
            "domain_id": str(run.domain_id) if run.domain_id else None,
            "status": run.status.value,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "error_message": run.error_message,
            "stats": run.stats,
        }
        for run in result.scalars().all()
    ]
