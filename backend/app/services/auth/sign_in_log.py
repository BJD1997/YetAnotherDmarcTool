"""Writes SignInEvent rows for every completed sign-in attempt across
auth.py's three flows. record_sign_in_event unconditionally bypasses RLS
(set_platform_admin_context) immediately before its insert rather than
trying to rely on whatever org context a given call site happens to already
have set — that state is genuinely inconsistent across auth.py's 12 write
points (some run before any org is known, some after, some under an
existing bypass for unrelated reasons), and in every one of them this
insert is the last DB operation before that request's own commit, so
widening the context here can't leak anything to a later read in the same
request."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_platform_admin_context
from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent


def client_network_info(request: Request) -> tuple[str | None, str | None]:
    return (
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )


async def record_sign_in_event(
    db: AsyncSession,
    *,
    result: SignInResult,
    auth_method: AuthMethod,
    ip_address: str | None,
    user_agent: str | None,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    attempted_email: str | None = None,
    failure_reason: str | None = None,
    created_at: datetime | None = None,
) -> None:
    await set_platform_admin_context(db, is_admin=True)
    db.add(
        SignInEvent(
            organization_id=organization_id,
            user_id=user_id,
            attempted_email=attempted_email,
            auth_method=auth_method,
            result=result,
            failure_reason=failure_reason,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=created_at or datetime.now(timezone.utc),
        )
    )
