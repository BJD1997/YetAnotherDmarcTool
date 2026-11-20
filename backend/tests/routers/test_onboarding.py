import pytest

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.models.mailbox_connection import MailboxConnection

from tests.conftest import login_as, seed_org_and_user


@pytest.mark.parametrize("entra", [False, True])
async def test_onboarding_status_fresh_org_has_no_mailbox_whatever_its_sign_in(api, entra):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=entra)
    await login_as(client, owner_factory, user)
    body = (await client.get("/api/onboarding/status")).json()
    assert body["has_domain"] is False
    assert body["has_verified_domain"] is False
    assert body["has_mailbox"] is False
    assert body["has_sign_in_provider"] is entra


async def test_onboarding_status_hosted_address_counts_as_mailbox(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(
            organization_id=org.id, name="hosted-only.example",
            verification_status=DomainVerificationStatus.verified,
            hosted_report_address="reports+abc123@hosted.example",
        ))
        await db.commit()
    await login_as(client, owner_factory, user)

    assert (await client.get("/api/onboarding/status")).json()["has_mailbox"] is True


async def test_onboarding_status_own_mailbox_counts_as_mailbox(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(MailboxConnection(organization_id=org.id, mailbox_address="dmarc@example.com"))
        await db.commit()
    await login_as(client, owner_factory, user)

    assert (await client.get("/api/onboarding/status")).json()["has_mailbox"] is True


async def test_onboarding_status_with_verified_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        db.add(Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified))
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body["has_domain"] is True
    assert body["has_verified_domain"] is True


async def test_onboarding_status_says_whether_the_server_has_a_hosted_mailbox(api, monkeypatch):
    from app.config import settings

    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=False)
    await login_as(client, owner_factory, user)
    for name in ("hosted_reports_mailbox_address", "hosted_reports_tenant_id", "entra_mail_client_id", "entra_mail_client_secret"):
        monkeypatch.setattr(settings, name, None)

    assert (await client.get("/api/onboarding/status")).json()["hosted_mailbox_ready"] is False

    monkeypatch.setattr(settings, "hosted_reports_mailbox_address", "reports@hosted.example")
    monkeypatch.setattr(settings, "hosted_reports_tenant_id", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(settings, "entra_mail_client_id", "client")
    assert (await client.get("/api/onboarding/status")).json()["hosted_mailbox_ready"] is False  # no secret yet

    monkeypatch.setattr(settings, "entra_mail_client_secret", "secret")
    assert (await client.get("/api/onboarding/status")).json()["hosted_mailbox_ready"] is True


async def test_wizard_is_finished_once_an_admin_reaches_the_last_step(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, entra=False)
    await login_as(client, owner_factory, user)

    assert (await client.get("/api/onboarding/status")).json()["wizard_completed"] is False

    first = await client.post("/api/onboarding/complete")
    again = await client.post("/api/onboarding/complete")

    assert first.status_code == again.status_code == 200
    assert (await client.get("/api/onboarding/status")).json()["wizard_completed"] is True


async def test_members_cant_finish_the_wizard(api):
    from app.models.enums import UserRole

    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, user)

    assert (await client.post("/api/onboarding/complete")).status_code == 403
