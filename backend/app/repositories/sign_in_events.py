import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent
from app.services.pagination import keyset_paginate


async def list_sign_in_events(
    db: AsyncSession,
    organization_id: uuid.UUID,
    *,
    limit: int,
    before_id: uuid.UUID | None,
    result: SignInResult | None,
    auth_method: AuthMethod | None,
) -> Sequence[SignInEvent]:
    """Keyset-paginated on (created_at, id) via keyset_paginate — see
    app/services/pagination.py. has_more is still computed by the router
    the same way it always was (len(events) == limit)."""
    query = select(SignInEvent).where(SignInEvent.organization_id == organization_id)
    if result is not None:
        query = query.where(SignInEvent.result == result)
    if auth_method is not None:
        query = query.where(SignInEvent.auth_method == auth_method)

    anchor_query = None
    if before_id is not None:
        anchor_query = select(SignInEvent.created_at, SignInEvent.id).where(
            SignInEvent.id == before_id, SignInEvent.organization_id == organization_id
        )

    rows, _has_more = await keyset_paginate(
        db, query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
        anchor_query=anchor_query, limit=limit,
    )
    return rows


async def count_sign_in_events_for_org(db: AsyncSession, organization_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(SignInEvent).where(SignInEvent.organization_id == organization_id)
    )
    return result.scalar_one()
