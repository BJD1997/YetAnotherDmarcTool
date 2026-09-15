import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent
from app.services.pagination import keyset_paginate

from tests.conftest import seed_org_and_user


async def _seed_events(owner_factory, org_id, count):
    async with owner_factory() as db:
        for i in range(count):
            db.add(
                SignInEvent(
                    organization_id=org_id,
                    attempted_email=f"user{i}@example.com",
                    auth_method=AuthMethod.local,
                    result=SignInResult.success,
                    created_at=datetime(2026, 1, i + 1, tzinfo=timezone.utc),
                )
            )
        await db.commit()


async def test_keyset_paginate_first_page_descending_has_more(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _seed_events(owner_factory, org.id, 3)

    async with owner_factory() as db:
        query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        rows, has_more = await keyset_paginate(
            db, query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=None, limit=2,
        )

    assert [r.attempted_email for r in rows] == ["user2@example.com", "user1@example.com"]
    assert has_more is True


async def test_keyset_paginate_second_page_exhausted(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _seed_events(owner_factory, org.id, 3)

    async with owner_factory() as db:
        first_query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        first_rows, _ = await keyset_paginate(
            db, first_query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=None, limit=2,
        )
        anchor_id = first_rows[-1].id

        second_query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        anchor_query = select(SignInEvent.created_at, SignInEvent.id).where(SignInEvent.id == anchor_id)
        rows, has_more = await keyset_paginate(
            db, second_query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=anchor_query, limit=2,
        )

    assert [r.attempted_email for r in rows] == ["user0@example.com"]
    assert has_more is False


async def test_keyset_paginate_stale_anchor_falls_back_to_first_page(api):
    """anchor_query resolving to no row (before_id points at a deleted or
    foreign row) returns the unfiltered first page rather than erroring —
    matches the existing hand-rolled behavior in list_sign_in_events."""
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _seed_events(owner_factory, org.id, 1)

    async with owner_factory() as db:
        query = select(SignInEvent).where(SignInEvent.organization_id == org.id)
        anchor_query = select(SignInEvent.created_at, SignInEvent.id).where(SignInEvent.id == uuid.uuid4())
        rows, has_more = await keyset_paginate(
            db, query, order_column=SignInEvent.created_at, id_column=SignInEvent.id,
            anchor_query=anchor_query, limit=2,
        )

    assert len(rows) == 1
    assert has_more is False


async def test_keyset_paginate_ascending_order(rls_sessions):
    """Admin Organizations pages alphabetically ascending, not newest-first
    — descending=False must reverse both the ORDER BY and the keyset
    comparison direction, not just the ORDER BY."""
    from app.db.rls import set_platform_admin_context
    from app.models.organization import Organization

    owner, db = rls_sessions
    # Seed with owner role (bypasses RLS) to create test data
    for name in ["Charlie Inc", "Alpha LLC", "Bravo Co"]:
        owner.add(Organization(name=name))
    await owner.commit()

    # Test with admin context on app role
    await set_platform_admin_context(db, is_admin=True)
    query = select(Organization).where(Organization.name.in_(["Charlie Inc", "Alpha LLC", "Bravo Co"]))
    rows, has_more = await keyset_paginate(
        db, query, order_column=Organization.name, id_column=Organization.id,
        anchor_query=None, limit=2, descending=False,
    )

    # Capture results while session is still active
    names = [r.name for r in rows]
    assert names == ["Alpha LLC", "Bravo Co"]
    assert has_more is True
