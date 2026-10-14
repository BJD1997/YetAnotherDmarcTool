"""GET /api/ask-ai/prompt: the Ask AI question for one issue (see
app/services/ask_ai/prompts.py). Only while the organization has turned
Ask AI on, and only for verified domains (same rule as senders)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.enums import DomainVerificationStatus
from app.models.user import User
from app.repositories.domains import get_owned_domain
from app.repositories.organizations import get_organization
from app.services.ask_ai.prompts import build_prompt

router = APIRouter(prefix="/ask-ai", tags=["ask-ai"])


@router.get("/prompt")
async def ask_ai_prompt(
    kind: Literal["dns_check", "sender", "compliance"],
    domain_id: uuid.UUID,
    subject: str | None = Query(None, max_length=255),
    issue: str | None = Query(None, max_length=500),
    # The period picked in the Senders list: a number of days or "all";
    # left out, the organization's rating window.
    period: str | None = Query(None, pattern=r"^(all|[1-9][0-9]{0,3})$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    org = await get_organization(db, user.organization_id)
    if not org.ask_ai_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ask AI is turned off for this organization")
    domain = await get_owned_domain(db, domain_id, user.organization_id)
    if domain.verification_status != DomainVerificationStatus.verified:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "domain isn't verified yet")
    try:
        prompt = await build_prompt(
            db, domain, kind, subject, issue, period=None if period is None else "all" if period == "all" else int(period)
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    # build_prompt may have cached new sender identities; nothing else is written.
    await db.commit()
    return {"prompt": prompt}
