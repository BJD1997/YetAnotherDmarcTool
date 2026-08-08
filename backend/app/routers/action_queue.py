import dataclasses
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.domain import Domain
from app.models.mailbox_connection import MailboxConnection
from app.models.user import User
from app.services.dmarc_analytics import service_breakdown
from app.services.action_queue.rules import (
    domain_ready_for_stricter_policy,
    enforcement_readiness_notice,
    high_volume_failure,
    likely_spoofed_sender,
    low_compliance_domain,
    mailbox_stopped_receiving_reports,
    parked_domain_not_locked_down,
    rua_destination_broken,
    sender_alignment_issue,
    spf_lookup_limit_risk,
    unknown_sender_above_threshold,
)

router = APIRouter(tags=["action-queue"])

_SEVERITY_ORDER = {"critical": 0, "serious": 1, "warning": 2, "good": 3, "neutral": 4}


@router.get("/action-queue")
async def action_queue(
    domain_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    if domain_id is not None:
        domain = await db.get(Domain, domain_id)
        if domain is None or domain.organization_id != user.organization_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "domain not found")
        domains = [domain]
    else:
        result = await db.execute(select(Domain).where(Domain.organization_id == user.organization_id))
        domains = result.scalars().all()

    items = list(await mailbox_stopped_receiving_reports(db, user.organization_id))

    # enforcement_readiness_notice is inherently org-wide (not "0 out of the
    # 1 domain you happen to have selected") — only evaluated, and only
    # added, when no domain_id filter is active. `domains` is already the
    # full org list in that case.
    if domain_id is None:
        items += await enforcement_readiness_notice(db, domains)

    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == user.organization_id))
    ).scalar_one_or_none()
    mailbox_address = connection.mailbox_address if connection is not None else None

    for domain in domains:
        # Computed once per domain and shared by the two rules that need a
        # per-service breakdown, rather than each calling service_breakdown
        # itself and doubling the aggregation query.
        services = await service_breakdown(db, domain.id)
        items += await unknown_sender_above_threshold(db, domain, services)
        items += await likely_spoofed_sender(db, domain, services)
        items += sender_alignment_issue(domain, services)
        items += await domain_ready_for_stricter_policy(db, domain)
        items += await low_compliance_domain(db, domain)
        items += await high_volume_failure(db, domain)
        items += await spf_lookup_limit_risk(db, domain)
        items += await rua_destination_broken(db, domain, mailbox_address)
        items += await parked_domain_not_locked_down(domain)

    # service_breakdown resolves any not-yet-cached source IPs as it goes
    # but never commits (see its docstring) — one commit here, after every
    # rule has run, persists those cache rows without dropping RLS context
    # mid-loop.
    await db.commit()

    # Category first (how urgent/high-signal the kind of problem is — see
    # CATEGORY_* in rules.py), severity second within a category. A
    # critical DNS-blocking item still sorts after a warning-level
    # high-volume-failure item, since the category itself already encodes
    # "this kind of problem matters more."
    items.sort(key=lambda i: (i.category, _SEVERITY_ORDER.get(i.severity, 5)))
    return [dataclasses.asdict(i) for i in items]
