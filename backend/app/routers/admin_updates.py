from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import AdminPrincipal, get_current_platform_admin
from app.services import update_check, updater_client

router = APIRouter(prefix="/admin/updates", tags=["platform-admin"])


@router.get("")
async def get_update_status(
    db: AsyncSession = Depends(get_db), _admin: AdminPrincipal = Depends(get_current_platform_admin)
) -> dict:
    state = await update_check.get_or_create_state(db)
    await db.commit()
    update_available = bool(state.latest_version and state.latest_version != settings.app_version)
    return {
        "running_version": settings.app_version,
        "latest_version": state.latest_version,
        "latest_release_url": state.latest_release_url,
        "latest_release_notes": state.latest_release_notes,
        "latest_published_at": state.latest_published_at.isoformat() if state.latest_published_at else None,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
        "check_error": state.check_error,
        "update_available": update_available,
    }


@router.post("/check-now")
async def check_now(_admin: AdminPrincipal = Depends(get_current_platform_admin)) -> dict:
    await update_check.run_update_check()
    return {"status": "checked"}


@router.post("/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger(_admin: AdminPrincipal = Depends(get_current_platform_admin)) -> dict:
    try:
        await updater_client.trigger_update()
    except updater_client.UpdaterUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return {"status": "update triggered"}
