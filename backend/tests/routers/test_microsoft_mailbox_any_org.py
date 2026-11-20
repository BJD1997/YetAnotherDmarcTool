"""A Microsoft 365 report mailbox for an organization that doesn't sign in
with Microsoft: its tenant is proven with a test sign-in, and a tenant
another organization uses is refused."""

import json
import uuid

from sqlalchemy import select

from app.config import settings
from app.models.enums import UserRole
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from tests.conftest import login_as, seed_org_and_user
from tests.routers.test_sign_in_settings import _break_glass, _fernet_key, fake_provider  # noqa: F401 (fixtures)


async def _mailbox_test(client, fake) -> dict:
    started = await client.post("/api/mailbox-connection/microsoft/test")
    assert started.status_code == 200, started.text
    redirect = await client.get(started.json()["start_url"], follow_redirects=False)
    assert redirect.status_code == 302
    fake["nonce"] = redirect.cookies.get("oauth_nonce")
    done = await client.get("/api/auth/callback", params={"code": "c", "state": redirect.cookies.get("oauth_state")}, follow_redirects=False)
    payload, _ = json.JSONDecoder().raw_decode(done.text, done.text.index("postMessage(") + len("postMessage("))
    return payload


async def _org_admin(client, owner_factory, monkeypatch):
    monkeypatch.setattr(settings, "entra_mail_client_id", "mail-app")
    org, _ = await _break_glass(client, owner_factory)  # local org admin, verified acme.example, no provider
    return org


async def test_an_org_without_microsoft_sign_in_proves_its_tenant(api, fake_provider, monkeypatch):
    client, owner_factory = api
    org = await _org_admin(client, owner_factory, monkeypatch)
    assert (await client.put("/api/mailbox-connection", json={"mailbox_address": "dmarc@acme.example"})).status_code == 409

    result = await _mailbox_test(client, fake_provider)
    assert result["ok"] is True, result
    saved = await client.put("/api/mailbox-connection", json={"mailbox_address": "dmarc@acme.example", "ticket": result["ticket"]})
    assert saved.status_code == 200, saved.text

    tenant = fake_provider["ms_claims"]["tid"]
    async with owner_factory() as db:
        connection = (await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == org.id))).scalar_one()
        assert str(connection.ms_tenant_id) == tenant
    current = (await client.get("/api/organizations/current")).json()
    assert current["sign_in_provider"] == "none" and current["microsoft_mailbox_available"] is True
    assert tenant in current["entra_consent_urls"]["mail_access_consent_url"]


async def test_another_orgs_tenant_is_refused_for_a_mailbox(api, fake_provider, monkeypatch):
    client, owner_factory = api
    taken = uuid.uuid4()
    async with owner_factory() as db:
        db.add(Organization(name="Owner", entra_tenant_id=taken))
        await db.commit()
    await _org_admin(client, owner_factory, monkeypatch)
    fake_provider["ms_claims"]["tid"] = str(taken)
    result = await _mailbox_test(client, fake_provider)
    assert result["ok"] is False and "another organization" in result["error"]


async def test_a_sign_in_ticket_doesnt_connect_a_mailbox(api, fake_provider, monkeypatch):
    from tests.routers.test_sign_in_settings import _run_test

    client, owner_factory = api
    await _org_admin(client, owner_factory, monkeypatch)
    sign_in = await _run_test(client, {"provider": "microsoft"}, "/api/auth/callback", fake_provider)
    assert sign_in["ok"] is True
    refused = await client.put("/api/mailbox-connection", json={"mailbox_address": "dmarc@acme.example", "ticket": sign_in["ticket"]})
    assert refused.status_code == 400


async def test_members_cant_start_the_mailbox_test(api, fake_provider):
    client, owner_factory = api
    _org, member = await seed_org_and_user(owner_factory, role=UserRole.member)
    await login_as(client, owner_factory, member)
    assert (await client.post("/api/mailbox-connection/microsoft/test")).status_code == 403
