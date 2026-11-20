"""The end of every provider sign-in (Microsoft, Google, OIDC), once the
provider's token has been checked: find or create the user, refuse
suspended organizations and disabled users, log it, start a session."""

import dataclasses
from datetime import datetime, timezone

from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context
from app.models.enums import AuthMethod, OrganizationStatus, SignInResult, UserRole, UserStatus
from app.models.organization import Organization
from app.models.user import User
from app.repositories.auth import get_user_by_subject
from app.repositories.domains import email_on_domains, verified_domain_names
from app.repositories.users import list_users_for_org
from app.services.auth import session_manager
from app.services.auth.session_manager import cookie_kwargs
from app.services.auth.sign_in_log import record_sign_in_event


@dataclasses.dataclass
class SignInOutcome:
    raw_token: str | None
    error: str | None


async def complete_sign_in(
    db: AsyncSession,
    *,
    org: Organization,
    method: AuthMethod,
    subject: str,
    email: str,
    display_name: str | None,
    ip_address: str | None,
    user_agent: str | None,
) -> SignInOutcome:
    await set_org_context(db, org.id)
    log = dict(auth_method=method, organization_id=org.id, attempted_email=email, ip_address=ip_address, user_agent=user_agent)

    if org.status == OrganizationStatus.suspended:
        await record_sign_in_event(db, result=SignInResult.failure, failure_reason="organization_suspended", **log)
        await db.commit()
        return SignInOutcome(None, "organization_suspended")

    user = await get_user_by_subject(db, org.id, method, subject)
    if user is None:
        user = await _account_from_previous_provider(db, org, method, subject, email)
    if user is None:
        # Only the expected admin becomes org admin, whichever order people
        # sign in; without one set, nobody does (the platform admin then
        # sets it or makes someone admin).
        expected = (org.expected_admin_email or "").strip().lower()
        is_expected_admin = bool(expected) and email.strip().lower() == expected
        user = User(
            organization_id=org.id,
            auth_method=method,
            email=email,
            display_name=display_name,
            role=UserRole.org_admin if is_expected_admin else UserRole.member,
        )
        if method == AuthMethod.entra:
            user.entra_object_id = subject
        else:
            user.external_subject = subject
        db.add(user)
        await db.flush()
    else:
        if user.status != UserStatus.active:
            await record_sign_in_event(
                db, result=SignInResult.failure, user_id=user.id, failure_reason="user_disabled", **log
            )
            await db.commit()
            return SignInOutcome(None, "user_disabled")
        user.email = email
        user.display_name = display_name

    user.last_login_at = datetime.now(timezone.utc)
    _, raw_token = await session_manager.create_user_session(
        db, user_id=user.id, organization_id=org.id, ip_address=ip_address, user_agent=user_agent
    )
    await record_sign_in_event(db, result=SignInResult.success, user_id=user.id, **log)
    await db.commit()
    return SignInOutcome(raw_token, None)


async def _account_from_previous_provider(
    db: AsyncSession, org: Organization, method: AuthMethod, subject: str, email: str
) -> User | None:
    """After the organization switched sign-in provider, someone's first
    sign-in through the new one takes over their account from the old one
    (same email address) instead of creating a second account: role, name
    and history carry over. Only an account of another provider qualifies,
    which can no longer sign in, never one of this provider or a local
    account, and only when the address is on one of the organization's
    verified domains and exactly one account has it. A disabled account
    stays disabled."""
    if not email_on_domains(email, await verified_domain_names(db, org.id)):
        return None
    address = email.strip().lower()
    candidates = [
        u
        for u in await list_users_for_org(db, org.id)
        if u.auth_method not in (method, AuthMethod.local) and u.email.strip().lower() == address
    ]
    if len(candidates) != 1:
        return None
    user = candidates[0]
    user.auth_method = method
    if method == AuthMethod.entra:
        user.entra_object_id, user.external_subject = subject, None
    else:
        user.entra_object_id, user.external_subject = None, subject
    await db.flush()
    return user


def session_redirect(outcome: SignInOutcome, *, clear_cookies: tuple[str, ...]) -> RedirectResponse:
    if outcome.error:
        response = RedirectResponse(f"/login?error={outcome.error}", status_code=302)
    else:
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            settings.session_cookie_name,
            outcome.raw_token,
            max_age=settings.session_idle_timeout_hours * 3600,
            **cookie_kwargs(),
        )
    for name in clear_cookies:
        response.delete_cookie(name, path="/")
    return response
