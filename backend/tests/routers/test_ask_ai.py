"""Ask AI endpoint: off until the organization turns it on, own verified
domains only; Action queue items say what to ask about."""

import pytest_asyncio

from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus, OrganizationStatus, UserRole
from app.models.organization import Organization
from app.services.ask_ai import prompts

from tests.conftest import login_as, seed_org_and_user
from tests.routers.test_action_queue import _add_sender


@pytest_asyncio.fixture(autouse=True)
async def _offline(monkeypatch):
    from app.services.source_identification import service_identifier

    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(service_identifier, "resolve_ptr", _none)
    async def _no_mx(*args, **kwargs):
        return []

    monkeypatch.setattr(prompts, "current_record", _none)
    monkeypatch.setattr(prompts, "mx_hosts", _no_mx)


async def _setup(owner_factory, *, verified=True):
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    async with owner_factory() as db:
        domain = Domain(
            organization_id=org.id, name="example.com",
            verification_status=DomainVerificationStatus.verified if verified else DomainVerificationStatus.pending,
        )
        db.add(domain)
        await db.commit()
    await _add_sender(
        owner_factory, org, domain, source_ip="203.0.113.80", count=100,
        disposition=Disposition.reject, dkim_result=AuthResult.fail, spf_result=AuthResult.fail,
    )
    return org, user, domain


async def test_off_until_turned_on(api):
    client, owner_factory = api
    _org, user, domain = await _setup(owner_factory)
    await login_as(client, owner_factory, user)
    params = {"kind": "sender", "domain_id": str(domain.id), "subject": "203.0.113.80"}

    assert (await client.get("/api/ask-ai/prompt", params=params)).status_code == 404
    await client.patch("/api/organizations/current", json={"name": "Org", "ask_ai_enabled": True})
    response = await client.get("/api/ask-ai/prompt", params=params)
    assert response.status_code == 200
    assert "203.0.113.80" in response.json()["prompt"]
    with_issue = await client.get("/api/ask-ai/prompt", params={**params, "issue": "Unreviewed sender"})
    assert "The issue: Unreviewed sender" in with_issue.json()["prompt"]

    assert (await client.get("/api/ask-ai/prompt", params={**params, "kind": "everything"})).status_code == 422
    assert (await client.get("/api/ask-ai/prompt", params={**params, "subject": "nobody"})).status_code == 404


async def test_unverified_and_foreign_domains_are_not_found(api):
    client, owner_factory = api
    _org, user, domain = await _setup(owner_factory, verified=False)
    async with owner_factory() as db:
        other = Organization(name="Other Org", status=OrganizationStatus.active, ask_ai_enabled=True)
        db.add(other)
        await db.flush()
        foreign = Domain(organization_id=other.id, name="other.example", verification_status=DomainVerificationStatus.verified)
        db.add(foreign)
        await db.commit()
    await login_as(client, owner_factory, user)
    await client.patch("/api/organizations/current", json={"name": "Org", "ask_ai_enabled": True})

    for domain_id in (domain.id, foreign.id):
        response = await client.get("/api/ask-ai/prompt", params={"kind": "compliance", "domain_id": str(domain_id)})
        assert response.status_code == 404


async def test_action_queue_items_say_what_to_ask(api):
    client, owner_factory = api
    _org, user, domain = await _setup(owner_factory)
    await login_as(client, owner_factory, user)

    items = (await client.get("/api/action-queue")).json()
    [sender_item] = [i for i in items if "203.0.113.80" in (i["link_path"] or "")]
    assert sender_item["ask_ai"] == {"kind": "sender", "subject": "203.0.113.80"}


async def test_sender_question_uses_the_period_picked_in_the_senders_list(api):
    client, owner_factory = api
    org, user, domain = await _setup(owner_factory)
    # Only seen 400 days ago: outside the default window, inside "all time".
    await _add_sender(owner_factory, org, domain, source_ip="198.51.100.7", count=20, days_ago=400)
    await login_as(client, owner_factory, user)
    await client.patch("/api/organizations/current", json={"name": "Org", "ask_ai_enabled": True})
    params = {"kind": "sender", "domain_id": str(domain.id), "subject": "198.51.100.7"}

    default = await client.get("/api/ask-ai/prompt", params=params)
    assert default.status_code == 404
    assert "last 90 days" in default.json()["detail"]
    assert (await client.get("/api/ask-ai/prompt", params={**params, "period": "365"})).status_code == 404

    all_time = await client.get("/api/ask-ai/prompt", params={**params, "period": "all"})
    assert all_time.status_code == 200
    assert '"198.51.100.7", all time: 20 messages' in all_time.json()["prompt"]
    assert (await client.get("/api/ask-ai/prompt", params={**params, "period": "500"})).status_code == 200

    assert (await client.get("/api/ask-ai/prompt", params={**params, "period": "0"})).status_code == 422
    assert (await client.get("/api/ask-ai/prompt", params={**params, "period": "forever"})).status_code == 422
