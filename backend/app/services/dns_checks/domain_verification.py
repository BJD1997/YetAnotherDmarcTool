import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_platform_admin_context
from app.db.session import async_session_factory
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.services.dns_checks.resolver import resolve_txt

logger = logging.getLogger(__name__)

VERIFICATION_LABEL = "_dmarc-dashboard-verify"


def verification_record_name(domain_name: str) -> str:
    return f"{VERIFICATION_LABEL}.{domain_name}"


async def verify_domain_ownership(domain_name: str, token: str) -> bool:
    values = await resolve_txt(verification_record_name(domain_name))
    return any(token in value for value in values)


async def apply_domain_verification(db: AsyncSession, domain: Domain) -> bool:
    """Runs the DNS TXT ownership check for one domain and, if it passes,
    marks it verified — propagating to any still-pending subdomains if this
    is an apex, same as before. Returns whether the domain ends this call
    verified. Does not commit — caller controls the transaction boundary.
    Shared by the manual POST /domains/{id}/verify endpoint and the
    background sweep below."""
    if domain.verification_status == DomainVerificationStatus.verified:
        return True
    if not await verify_domain_ownership(domain.name, domain.verification_token):
        return False

    now = datetime.now(timezone.utc)
    domain.verification_status = DomainVerificationStatus.verified
    domain.verified_at = now

    if domain.parent_domain_id is None:
        await db.execute(
            Domain.__table__.update()
            .where(
                Domain.parent_domain_id == domain.id,
                Domain.verification_status == DomainVerificationStatus.pending,
            )
            .values(verification_status=DomainVerificationStatus.verified, verified_at=now)
        )
    return True


async def run_domain_verification_sweep() -> None:
    """Background counterpart to POST /domains/{id}/verify — DNS TXT
    propagation is often minutes to an hour, so most domains end up
    verified here rather than by the user clicking "Check now" at exactly
    the right moment. Cross-org in one sweep, same is_platform_admin
    bypass forensic_purge.run_retention_purge uses."""
    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        pending = (
            (await db.execute(select(Domain).where(Domain.verification_status == DomainVerificationStatus.pending)))
            .scalars()
            .all()
        )
        verified_count = 0
        for domain in pending:
            if await apply_domain_verification(db, domain):
                verified_count += 1
        if verified_count:
            await db.commit()
            logger.info("domain verification sweep: verified %d domain(s)", verified_count)
        else:
            await db.rollback()
