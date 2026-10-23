# backend/tests/repositories/test_mailbox_connections.py
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.repositories.mailbox_connections import (
    get_or_create_hosted_reports_poll_state,
    list_orgs_with_granted_mailbox_connections,
)

from tests.conftest import seed_org_and_user


async def test_list_orgs_with_granted_mailbox_connections(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory, entra=True)

    async with owner_factory() as db:
        db.add(MailboxConnection(
            organization_id=org.id, consent_status=ConsentStatus.granted, mailbox_address="reports@example.com",
            ms_tenant_id=org.entra_tenant_id,
        ))
        await db.commit()

    async with owner_factory() as db:
        results = await list_orgs_with_granted_mailbox_connections(db)
    assert (org.id, org.entra_tenant_id) in [tuple(r) for r in results]


async def test_a_microsoft_mailbox_keeps_polling_after_sign_in_moves_away(api):
    # The mailbox has its own tenant: the organization's sign-in can switch
    # to Google or OIDC without breaking report ingestion.
    import uuid

    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)  # no Microsoft sign-in
    tenant = uuid.uuid4()
    async with owner_factory() as db:
        db.add(MailboxConnection(
            organization_id=org.id, consent_status=ConsentStatus.granted, mailbox_address="reports@example.com",
            ms_tenant_id=tenant,
        ))
        await db.commit()

    async with owner_factory() as db:
        results = await list_orgs_with_granted_mailbox_connections(db)
    assert (org.id, tenant) in [tuple(r) for r in results]


async def test_get_or_create_hosted_reports_poll_state_creates_once(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        first = await get_or_create_hosted_reports_poll_state(db)
        await db.commit()
        first_id = first.id

    async with owner_factory() as db:
        second = await get_or_create_hosted_reports_poll_state(db)
    assert second.id == first_id
