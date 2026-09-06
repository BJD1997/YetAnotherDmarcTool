from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.platform_admin import PlatformAdmin
from app.models.platform_admin_mfa_pending_challenge import PlatformAdminMfaPendingChallenge
from app.models.platform_admin_recovery_code import PlatformAdminRecoveryCode


async def get_platform_admin_by_email(db: AsyncSession, email: str) -> PlatformAdmin | None:
    result = await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))
    return result.scalar_one_or_none()


async def get_admin_mfa_pending_challenge(db: AsyncSession, token_hash: str) -> PlatformAdminMfaPendingChallenge | None:
    result = await db.execute(
        select(PlatformAdminMfaPendingChallenge).where(PlatformAdminMfaPendingChallenge.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def get_unused_admin_recovery_code(
    db: AsyncSession, platform_admin_id: UUID, code_hash: str
) -> PlatformAdminRecoveryCode | None:
    result = await db.execute(
        select(PlatformAdminRecoveryCode).where(
            PlatformAdminRecoveryCode.platform_admin_id == platform_admin_id,
            PlatformAdminRecoveryCode.code_hash == code_hash,
            PlatformAdminRecoveryCode.used_at.is_(None),
        )
    )
    return result.scalar_one_or_none()
