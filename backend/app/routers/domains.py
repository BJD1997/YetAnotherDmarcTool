import re
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, CheckType, DomainMailProfile, DomainVerificationStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.models.user import User
from app.services.cloudflare.dns_provisioner import ensure_authorization_record
from app.services.dns_checks.dmarc_record import check_rua_destination
from app.services.dns_checks.domain_verification import apply_domain_verification, verification_record_name
from app.services.ingestion.report_writer import resweep_domain_records, resweep_unmatched_reports
from app.services.rating.domain_rating import compute_domain_rating, domain_policy_readiness, latest_findings_by_type
from app.services.rating.score import tally_worst_status

router = APIRouter(prefix="/domains", tags=["domains"])

_DOMAIN_NAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class DomainCreateRequest(BaseModel):
    name: str
    parent_domain_id: uuid.UUID | None = None
    notes: str | None = None
    mail_profile: DomainMailProfile | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not _DOMAIN_NAME_RE.match(value):
            raise ValueError("not a valid domain name")
        return value


class DomainUpdateRequest(BaseModel):
    notes: str | None = None
    is_active: bool | None = None
    mail_profile: DomainMailProfile | None = None


def _domain_out(domain: Domain) -> dict:
    return {
        "id": str(domain.id),
        "name": domain.name,
        "parent_domain_id": str(domain.parent_domain_id) if domain.parent_domain_id else None,
        "notes": domain.notes,
        "is_active": domain.is_active,
        "created_at": domain.created_at.isoformat(),
        "verification_status": domain.verification_status.value,
        "verified_at": domain.verified_at.isoformat() if domain.verified_at else None,
        # Only meaningful while pending, but harmless (and convenient for the
        # UI) to include once verified too, rather than making callers guess.
        "verification_token": domain.verification_token,
        "verification_record_name": verification_record_name(domain.name),
        "mail_profile": domain.mail_profile.value,
        "hosted_report_address": domain.hosted_report_address,
    }


@router.get("")
async def list_domains(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    result = await db.execute(
        select(Domain).where(Domain.organization_id == user.organization_id).order_by(Domain.name)
    )
    return [_domain_out(d) for d in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_domain(
    body: DomainCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    parent: Domain | None = None
    if body.parent_domain_id is not None:
        parent = await db.get(Domain, body.parent_domain_id)
        if parent is None or parent.organization_id != user.organization_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "parent_domain_id not found in your organization")
        if parent.parent_domain_id is not None:
            # Only one level of nesting: apex domain -> subdomains. Prevents
            # building arbitrarily deep/cyclic trees the UI isn't designed for.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "parent_domain_id must itself be an apex domain, not a subdomain"
            )
        if not body.name.endswith(f".{parent.name}"):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{body.name!r} is not a subdomain of {parent.name!r}"
            )

    domain = Domain(
        organization_id=user.organization_id,
        parent_domain_id=body.parent_domain_id,
        name=body.name,
        notes=body.notes,
        mail_profile=body.mail_profile or DomainMailProfile.sends_mail,
    )
    if parent is not None and parent.verification_status == DomainVerificationStatus.verified:
        # Proving control of the apex zone's DNS implies control of
        # everything under it — a subdomain of an already-verified apex
        # doesn't need its own separate TXT challenge.
        domain.verification_status = DomainVerificationStatus.verified
        domain.verified_at = datetime.now(timezone.utc)

    db.add(domain)
    try:
        # flush (not commit) so the row is visible to the refresh() SELECT
        # below within the SAME transaction — the RLS context set by
        # require_org_admin/get_current_user via SET LOCAL only lasts for
        # that one transaction, so a commit here would make a subsequent
        # refresh() run in a new, unscoped transaction and find nothing.
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "this domain is already registered")

    # Reports that arrived before this domain was registered are stuck with
    # domain_id=NULL in the unmatched bucket — re-check them now that a
    # domain exists to match against, rather than leaving them stranded
    # until the next unrelated write happens to touch them.
    resweep_counts = await resweep_unmatched_reports(db, user.organization_id)
    # Separately, records may have already been attributed to a less-specific
    # ancestor (e.g. this subdomain's mail counted under its parent, since
    # nothing more specific existed to match yet) — re-resolve those by their
    # own header_from now that this domain exists to match against too. Also
    # doubles as a backfill when called for an already-registered domain.
    reattributed_records = await resweep_domain_records(db, user.organization_id, domain)

    await db.refresh(domain)
    await db.commit()
    return {
        **_domain_out(domain),
        "reattributed_reports": resweep_counts["aggregate_reports"],
        "reattributed_records": reattributed_records,
    }


@router.get("/ranked")
async def ranked_domains(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    """Every domain in the org with the operational summary the Domains
    list page and Overview's "needing attention" list both need — scored
    via compute_domain_rating, sorted worst-first. Registered before
    /{domain_id} below so "ranked" isn't swallowed by that route's
    uuid.UUID path converter (which would 422 rather than fall through).
    Unverified domains sort first — nothing has even been checked yet,
    which is a bigger unknown than a low score on a domain that has been
    checked. Verified-but-no-traffic domains sort last — nothing's wrong,
    there's just nothing to score yet."""
    result = await db.execute(
        select(Domain).where(Domain.organization_id == user.organization_id).order_by(Domain.name)
    )
    domains = result.scalars().all()

    dmarc_pass = (DmarcAggregateRecord.dkim_result == AuthResult.pass_) | (
        DmarcAggregateRecord.spf_result == AuthResult.pass_
    )

    # Fetched once, not per-domain — only actually used below for domains
    # that turn out to have no report data yet (see the no-data signals).
    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == user.organization_id))
    ).scalar_one_or_none()
    mailbox_address = connection.mailbox_address if connection is not None else None

    items = []
    for domain in domains:
        not_verified = domain.verification_status != DomainVerificationStatus.verified
        score: float | None = None
        grade: str | None = None
        insufficient_data = True
        message_volume = 0
        check_status_counts = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
        ready_to_enforce = False
        current_policy: str | None = None
        # A domain with no report data yet may be fine (just not sending
        # mail), misconfigured (no DMARC record, or rua= pointed wrong), or
        # simply not checked yet — these three signals let the UI tell
        # those apart instead of a bare "no data yet." Only computed for
        # verified-but-no-data domains (see below); a not_verified domain
        # already gets its own, more directly actionable "verify first" UI.
        dmarc_configured: bool | None = None
        rua_status: str | None = None
        last_dns_check_at = None

        if not not_verified:
            findings_by_type = await latest_findings_by_type(db, domain.id)
            check_status_counts = tally_worst_status(findings_by_type)
            rating, message_volume = await compute_domain_rating(db, domain, findings_by_type=findings_by_type)
            score = rating.score
            grade = rating.grade
            insufficient_data = rating.insufficient_data

            readiness = await domain_policy_readiness(db, domain, rating=rating, total_volume=message_volume)
            current_policy = readiness.latest_policy
            ready_to_enforce = readiness.ready

            if insufficient_data:
                dmarc_findings = findings_by_type.get(CheckType.dmarc, [])
                if dmarc_findings:
                    dmarc_configured = not (
                        len(dmarc_findings) == 1 and dmarc_findings[0].summary == "No DMARC record found"
                    )
                    last_dns_check_at = dmarc_findings[0].checked_at
                elif findings_by_type:
                    last_dns_check_at = next(iter(findings_by_type.values()))[0].checked_at

                if mailbox_address is not None:
                    rua_result = await check_rua_destination(domain.name, mailbox_address)
                    rua_status = rua_result.status

        last_report_at = (
            await db.execute(
                select(func.max(DmarcAggregateReport.received_at)).where(DmarcAggregateReport.domain_id == domain.id)
            )
        ).scalar_one_or_none()
        failed_volume = (
            await db.execute(
                select(func.coalesce(func.sum(case((~dmarc_pass, DmarcAggregateRecord.count), else_=0)), 0)).where(
                    DmarcAggregateRecord.domain_id == domain.id
                )
            )
        ).scalar_one()

        items.append(
            {
                "domain_id": str(domain.id),
                "name": domain.name,
                "not_verified": not_verified,
                "insufficient_data": insufficient_data,
                "score": score,
                "grade": grade,
                "message_volume": message_volume,
                "failed_volume": int(failed_volume),
                "current_policy": current_policy,
                "last_report_at": last_report_at.isoformat() if last_report_at else None,
                "check_status_counts": check_status_counts,
                "ready_to_enforce": ready_to_enforce,
                "dmarc_configured": dmarc_configured,
                "rua_status": rua_status,
                "last_dns_check_at": last_dns_check_at.isoformat() if last_dns_check_at else None,
                "mail_profile": domain.mail_profile.value,
            }
        )

    def sort_key(item: dict) -> tuple:
        if item["not_verified"]:
            return (0, 0.0)
        if item["insufficient_data"]:
            return (2, 0.0)
        return (1, item["score"])

    items.sort(key=sort_key)
    return items


@router.get("/{domain_id}")
async def get_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    return _domain_out(domain)


@router.post("/{domain_id}/verify")
async def verify_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")

    ok = await apply_domain_verification(db, domain)
    if not ok:
        return {"verified": False, "domain": _domain_out(domain)}

    await db.flush()
    await db.refresh(domain)
    await db.commit()
    return {"verified": True, "domain": _domain_out(domain)}


@router.patch("/{domain_id}")
async def update_domain(
    domain_id: uuid.UUID,
    body: DomainUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_org_admin),
) -> dict:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
    if body.notes is not None:
        domain.notes = body.notes
    if body.is_active is not None:
        domain.is_active = body.is_active
    if body.mail_profile is not None:
        domain.mail_profile = body.mail_profile
    await db.flush()
    await db.refresh(domain)
    await db.commit()
    return _domain_out(domain)


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> None:
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")

    subdomain_count = await db.execute(
        select(func.count()).select_from(Domain).where(Domain.parent_domain_id == domain_id)
    )
    if subdomain_count.scalar_one() > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "remove or reassign subdomains first")

    report_count = await db.execute(
        select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.domain_id == domain_id)
    )
    if report_count.scalar_one() > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "domain has report history — archive it instead (PATCH is_active=false)"
        )

    await db.delete(domain)
    await db.commit()


def _hosted_mailbox_available(org: Organization) -> bool:
    """Local-auth orgs (no entra_tenant_id) have no Entra tenant to grant
    Mail Access consent from, so an operator-hosted mailbox is their only
    way to receive reports at all — always available to them. Entra orgs
    CAN set up their own MailboxConnection, so hosted mailbox is an
    explicit opt-in convenience for them instead (see Settings)."""
    return org.entra_tenant_id is None or org.hosted_mailbox_opt_in


@router.post("/{domain_id}/hosted-report-address")
async def get_or_create_hosted_report_address(
    domain_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    """Generates (idempotently returns if already generated) an
    operator-hosted rua= address for customers with no mailbox of their
    own to dedicate — see app/workers/jobs/hosted_reports_poll_job.py for
    how mail sent to it gets attributed back to this domain."""
    domain = await db.get(Domain, domain_id)
    if domain is None or domain.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")

    org = await db.get(Organization, user.organization_id)
    if not _hosted_mailbox_available(org):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "hosted mailbox isn't enabled for your organization — see Settings")

    if not settings.hosted_reports_address_domain or not settings.hosted_reports_mailbox_address:
        raise HTTPException(status.HTTP_409_CONFLICT, "hosted reporting addresses aren't configured on this instance yet")

    if domain.hosted_report_address is None:
        # Plus-addressing, not a catch-all: most providers (Exchange Online
        # included) will only route mail-not-otherwise-deliverable to
        # <local>+<tag>@domain if <local>@domain is a real mailbox — an
        # address with an arbitrary random local part (the old scheme here)
        # never resolves anywhere without a domain-wide catch-all, which
        # isn't always available (see hosted_reports_poll_job.py, which
        # only ever reads hosted_reports_mailbox_address's own inbox). So
        # the local part before "+" must be that mailbox's own name —
        # hosted_reports_address_domain therefore has to be the same domain
        # as hosted_reports_mailbox_address for this to actually deliver.
        mailbox_local_part = settings.hosted_reports_mailbox_address.split("@", 1)[0]
        domain.hosted_report_address = f"{mailbox_local_part}+{secrets.token_hex(6)}@{settings.hosted_reports_address_domain}"
        await db.flush()
        await db.refresh(domain)
        await db.commit()

    # Every call (not just first creation) — cheap and idempotent, so a
    # transient Cloudflare failure self-heals on the next call instead of
    # requiring separate retry UI.
    provision_result = await ensure_authorization_record(domain.name)

    return {
        "hosted_report_address": domain.hosted_report_address,
        "authorization_record_status": provision_result.status,
        "authorization_record_detail": provision_result.detail,
    }
