"""Run once by the `migrate` one-off container, after bootstrap_platform_admin,
on the *separate* demo deployment only (docker-compose.demo.yml) — a no-op
if DEMO_LOGIN_EMAIL/PASSWORD aren't set, so this is always safe to include
in the same migrate command as the real instance without side effects there.

Creates exactly one organization (flagged is_demo_read_only — see
enforce_demo_read_only in app/main.py and the TOTP bypass in
POST /auth/local-login) with one local-auth user and the project's own
domain already verified, if none of that exists yet. A no-op on any later
run once the org exists, so restarting/redeploying the demo doesn't
recreate or duplicate anything.

Also backfills a hosted_report_address on the demo domain whenever
HOSTED_REPORTS_MAILBOX_ADDRESS/HOSTED_REPORTS_ADDRESS_DOMAIN are
configured — same plus-addressing scheme as
POST /domains/{id}/hosted-report-address, duplicated here rather than
reusing that endpoint since this script runs outside any HTTP/auth
context. Runs on every migrate, not just first creation, so turning on
HOSTED_REPORTS_* after the demo already exists still takes effect on the
next deploy instead of needing a one-off manual fix.

Also registers the same DKIM selectors (selector1/selector2 — Microsoft
365's standard pair, already live on yetanotherdmarctool.com's real DNS)
the real deployment has, since they're not discoverable via DNS alone
and the DKIM checker has nothing to check without them.

Also sets spf_all_qualifier_mode to conditional, matching the real
deployment's org — it's normally a Settings toggle, but the demo org is
is_demo_read_only so a visitor (or the operator, via the demo's own UI)
can't change it there."""

import asyncio
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db.rls import set_platform_admin_context, set_org_context
from app.db.session import async_session_factory
from app.models.dkim_selector import DkimSelector
from app.models.domain import Domain
from app.models.enums import AuthMethod, DomainVerificationStatus, SpfAllQualifierMode, UserRole, UserStatus
from app.models.organization import Organization
from app.models.user import User
from app.services.auth.password import hash_password

DKIM_SELECTORS = ["selector1", "selector2"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_demo")

DEMO_DOMAIN = "yetanotherdmarctool.com"


async def main() -> None:
    if not settings.demo_login_email or not settings.demo_login_password:
        logger.info("DEMO_LOGIN_EMAIL/PASSWORD not set; skipping demo seed")
        return

    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        result = await db.execute(select(Organization).where(Organization.is_demo_read_only.is_(True)))
        org = result.scalar_one_or_none()

        if org is None:
            org = Organization(name="Demo", is_demo_read_only=True)
            db.add(org)
            await db.flush()

            await set_org_context(db, org.id)

            user = User(
                organization_id=org.id,
                email=settings.demo_login_email,
                role=UserRole.org_admin,
                status=UserStatus.active,
                auth_method=AuthMethod.local,
                password_hash=hash_password(settings.demo_login_password),
            )
            db.add(user)

            domain = Domain(
                organization_id=org.id,
                name=DEMO_DOMAIN,
                # Already verified in the real deployment (this is the
                # project's own domain) — no need to re-run the TXT-record
                # dance against ourselves in a seed script.
                verification_status=DomainVerificationStatus.verified,
                verified_at=datetime.now(timezone.utc),
            )
            db.add(domain)

            await db.commit()
            logger.info("seeded demo org %s with domain %s and login %s", org.id, DEMO_DOMAIN, settings.demo_login_email)
        else:
            await set_org_context(db, org.id)
            logger.info("a demo org already exists; skipping org/user/domain creation")

        if org.spf_all_qualifier_mode != SpfAllQualifierMode.conditional:
            org.spf_all_qualifier_mode = SpfAllQualifierMode.conditional
            await db.commit()
            logger.info("set spf_all_qualifier_mode=conditional for demo org")

        if settings.hosted_reports_mailbox_address and settings.hosted_reports_address_domain:
            domain = (
                await db.execute(select(Domain).where(Domain.organization_id == org.id, Domain.name == DEMO_DOMAIN))
            ).scalar_one_or_none()
            if domain is not None and domain.hosted_report_address is None:
                mailbox_local_part = settings.hosted_reports_mailbox_address.split("@", 1)[0]
                domain.hosted_report_address = f"{mailbox_local_part}+{secrets.token_hex(6)}@{settings.hosted_reports_address_domain}"
                await db.commit()
                logger.info("generated hosted_report_address %s for demo domain", domain.hosted_report_address)

        domain = (
            await db.execute(select(Domain).where(Domain.organization_id == org.id, Domain.name == DEMO_DOMAIN))
        ).scalar_one_or_none()
        if domain is not None:
            existing_selectors = set(
                (
                    await db.execute(select(DkimSelector.selector).where(DkimSelector.domain_id == domain.id))
                ).scalars()
            )
            for selector in DKIM_SELECTORS:
                if selector not in existing_selectors:
                    db.add(DkimSelector(organization_id=org.id, domain_id=domain.id, selector=selector))
                    logger.info("registered DKIM selector %s for demo domain", selector)
            await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
