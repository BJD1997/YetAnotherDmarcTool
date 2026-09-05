import uuid
from collections.abc import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent


async def list_sign_in_events(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    limit: int,
    before_id: uuid.UUID | None,
    result: SignInResult | None,
    auth_method: AuthMethod | None,
) -> Sequence[SignInEvent]:
    """Keyset-paginated on (created_at, id), same shape as dmarc_reports_by_day,
    rather than OFFSET — this table only grows, and an admin scrolling through
    pages shouldn't see rows shift around as new sign-ins land between requests."""
    query = select(SignInEvent).where(SignInEvent.organization_id == organization_id)
    if result is not None:
        query = query.where(SignInEvent.result == result)
    if auth_method is not None:
        query = query.where(SignInEvent.auth_method == auth_method)

    if before_id is not None:
        anchor = (
            await db.execute(
                select(SignInEvent.created_at, SignInEvent.id).where(
                    SignInEvent.id == before_id, SignInEvent.organization_id == organization_id
                )
            )
        ).first()
        if anchor is not None:
            query = query.where(tuple_(SignInEvent.created_at, SignInEvent.id) < anchor)

    query = query.order_by(SignInEvent.created_at.desc(), SignInEvent.id.desc()).limit(limit)
    result_rows = await db.execute(query)
    return result_rows.scalars().all()
