from app.models.enums import UserRole

from tests.conftest import login_as, seed_org_and_user


async def test_get_mailbox_connection_not_found(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/mailbox-connection")

    assert response.status_code == 404


async def test_set_mailbox_connection_requires_entra_tenant(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=False)
    await login_as(client, owner_factory, user)

    response = await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    assert response.status_code == 409


async def test_set_mailbox_connection(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)

    response = await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["mailbox_address"] == "reports@example.com"
    assert body["consent_status"] == "granted"


async def test_get_mailbox_connection_after_set(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)
    await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})

    response = await client.get("/api/mailbox-connection")

    assert response.status_code == 200
    assert response.json()["mailbox_address"] == "reports@example.com"


async def test_mailbox_job_runs_empty(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/mailbox-connection/job-runs")

    assert response.status_code == 200
    assert response.json() == []


async def test_connect_and_resync_hand_the_sync_to_the_worker(api, monkeypatch):
    """Regression: the api used to sync the mailbox itself (a BackgroundTask)
    right after connecting and on Resync. On Azure the api doesn't hold the
    Graph app's secret, so that sync failed with AADSTS7000216. The worker
    holds it, so the api now only queues the sync."""
    from sqlalchemy import text

    import app.workers.jobs.mailbox_poll_job as mailbox_poll_job

    async def _must_not_run_in_api(*args, **kwargs):
        raise AssertionError("the api must not sync the mailbox itself")

    monkeypatch.setattr(mailbox_poll_job, "poll_org_mailbox", _must_not_run_in_api)
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)

    async def queued() -> list[tuple]:
        async with owner_factory() as db:
            rows = await db.execute(
                text("SELECT job_type, status, payload FROM background_jobs WHERE dedupe_key = :k"),
                {"k": f"mailbox_poll:{org.id}"},
            )
            return [tuple(r) for r in rows]

    assert (await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})).status_code == 200
    jobs = await queued()
    assert jobs == [("mailbox_poll", "pending", {"org_id": str(org.id), "tenant_id": str(org.entra_tenant_id)})]

    # Resync while that sync is still queued: no second job.
    assert (await client.post("/api/mailbox-connection/resync")).status_code == 202
    assert len(await queued()) == 1

    async with owner_factory() as db:
        await db.execute(text("DELETE FROM background_jobs WHERE dedupe_key = :k"), {"k": f"mailbox_poll:{org.id}"})
        await db.commit()


async def test_a_microsoft_mailbox_keeps_its_tenant_when_sign_in_moves(api):
    from sqlalchemy import select, text

    from app.models.mailbox_connection import MailboxConnection
    from app.models.organization import Organization

    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin, entra=True)
    await login_as(client, owner_factory, user)
    assert (await client.put("/api/mailbox-connection", json={"mailbox_address": "reports@example.com"})).status_code == 200
    tenant = org.entra_tenant_id

    # The organization switches its sign-in away from Microsoft.
    async with owner_factory() as db:
        o = await db.get(Organization, org.id)
        o.entra_tenant_id = None
        await db.execute(text("DELETE FROM background_jobs WHERE dedupe_key = :k"), {"k": f"mailbox_poll:{org.id}"})
        await db.commit()

    assert (await client.post("/api/mailbox-connection/resync")).status_code == 202
    # Changing the address keeps the mailbox's tenant too.
    assert (await client.put("/api/mailbox-connection", json={"mailbox_address": "dmarc@example.com"})).status_code == 200
    async with owner_factory() as db:
        connection = (await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org.id))).scalar_one()
        assert connection.ms_tenant_id == tenant
