# backend/tests/repositories/test_domains.py
import uuid
from datetime import datetime, timezone

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.repositories.domains import (
    get_domain_by_org_and_name,
    get_domain_id_by_org_and_name,
    list_domains_with_hosted_report_address,
    list_pending_domains,
    mark_pending_subdomains_verified,
)

from tests.conftest import seed_org_and_user


async def _add_domain(owner_factory, org, **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_get_domain_id_by_org_and_name_found_and_not_found(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org, name="example.com")

    async with owner_factory() as db:
        found = await get_domain_id_by_org_and_name(db, org.id, "example.com")
        missing = await get_domain_id_by_org_and_name(db, org.id, "nope.example")

    assert found == domain.id
    assert missing is None


async def test_get_domain_by_org_and_name_returns_full_object(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org, name="example.com")

    async with owner_factory() as db:
        result = await get_domain_by_org_and_name(db, org.id, "example.com")

    assert result is not None
    assert result.name == "example.com"


async def test_list_domains_with_hosted_report_address(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org, name="a.example", hosted_report_address="a@reports.example")
    await _add_domain(owner_factory, org, name="b.example")

    async with owner_factory() as db:
        results = await list_domains_with_hosted_report_address(db)

    names = {d.name for d in results}
    assert "a.example" in names
    assert "b.example" not in names


async def test_list_pending_domains(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    await _add_domain(owner_factory, org, name="pending.example", verification_status=DomainVerificationStatus.pending)
    await _add_domain(owner_factory, org, name="verified.example", verification_status=DomainVerificationStatus.verified)

    async with owner_factory() as db:
        results = await list_pending_domains(db)

    names = {d.name for d in results}
    assert "pending.example" in names
    assert "verified.example" not in names


async def test_mark_pending_subdomains_verified(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    parent = await _add_domain(owner_factory, org, name="example.com", verification_status=DomainVerificationStatus.verified)
    sub_pending = await _add_domain(
        owner_factory, org, name="sub.example.com", parent_domain_id=parent.id,
        verification_status=DomainVerificationStatus.pending,
    )
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        await mark_pending_subdomains_verified(db, parent.id, now)
        await db.commit()

    async with owner_factory() as db:
        refreshed = await db.get(Domain, sub_pending.id)
    assert refreshed.verification_status == DomainVerificationStatus.verified
    assert refreshed.verified_at is not None
