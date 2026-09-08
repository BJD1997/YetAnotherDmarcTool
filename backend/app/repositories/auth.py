from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuthMethod
from app.models.mfa_pending_challenge import MfaPendingChallenge
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.platform_admin_session import PlatformAdminSession
from app.models.session import UserSession
from app.models.user import User
from app.models.user_recovery_code import UserRecoveryCode


async def get_organization_by_entra_tenant_id(db: AsyncSession, tenant_id: str) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.entra_tenant_id == tenant_id))
    return result.scalar_one_or_none()


async def get_user_by_org_and_entra_object_id(db: AsyncSession, organization_id: UUID, object_id: str) -> User | None:
    result = await db.execute(
        select(User).where(User.organization_id == organization_id, User.entra_object_id == object_id)
    )
    return result.scalar_one_or_none()


async def count_users_for_org(db: AsyncSession, organization_id: UUID) -> int:
    result = await db.execute(select(func.count()).select_from(User).where(User.organization_id == organization_id))
    return result.scalar_one()


async def get_local_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower(), User.auth_method == AuthMethod.local)
    )
    return result.scalar_one_or_none()


async def get_mfa_pending_challenge(db: AsyncSession, token_hash: str) -> MfaPendingChallenge | None:
    result = await db.execute(select(MfaPendingChallenge).where(MfaPendingChallenge.token_hash == token_hash))
    return result.scalar_one_or_none()


async def get_unused_recovery_code(db: AsyncSession, user_id: UUID, code_hash: str) -> UserRecoveryCode | None:
    result = await db.execute(
        select(UserRecoveryCode).where(
            UserRecoveryCode.user_id == user_id,
            UserRecoveryCode.code_hash == code_hash,
            UserRecoveryCode.used_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_password_setup_token(db: AsyncSession, token_hash: str) -> PasswordSetupToken | None:
    result = await db.execute(select(PasswordSetupToken).where(PasswordSetupToken.token_hash == token_hash))
    return result.scalar_one_or_none()


async def get_user_session_by_token_hash(db: AsyncSession, token_hash: str) -> UserSession | None:
    result = await db.execute(select(UserSession).where(UserSession.session_token_hash == token_hash))
    return result.scalar_one_or_none()


async def get_platform_admin_session_by_token_hash(db: AsyncSession, token_hash: str) -> PlatformAdminSession | None:
    result = await db.execute(select(PlatformAdminSession).where(PlatformAdminSession.session_token_hash == token_hash))
    return result.scalar_one_or_none()
