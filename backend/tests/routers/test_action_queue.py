import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import AuthResult, CheckStatus, CheckType, Disposition, DomainVerificationStatus, UserRole

from tests.conftest import login_as, seed_org_and_user


@pytest_asyncio.fixture(autouse=True)
async def _fast_ip_fallback(monkeypatch):
    """Same idiom as test_dmarc_reports.py's fixture of the same name: every
    source_ip here resolves via identify_many, which does a real reverse-DNS
    lookup unless patched — see that file for the full rationale."""
    from app.services.source_identification import service_identifier

    async def _no_ptr(ip: str) -> str | None:
        return None

    monkeypatch.setattr(service_identifier, "resolve_ptr", _no_ptr)


async def _add_domain(owner_factory, org, *, name: str = "example.com", **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name=name, **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def _add_sender(
    owner_factory,
    org,
    domain,
    *,
    source_ip: str,
    count: int,
    disposition: Disposition = Disposition.none,
    dkim_result: AuthResult = AuthResult.pass_,
    spf_result: AuthResult = AuthResult.pass_,
) -> None:
    """Seeds one DMARC report + record from `source_ip`, enough for
    service_breakdown/service_breakdown_multi to surface it as a sender —
    same minimal shape as test_dmarc_reports.py's fixtures, but collapsed
    into one call since these tests only need a single record per sender."""
    now = datetime.now(timezone.utc)
    async with owner_factory() as db:
        report = DmarcAggregateReport(
            organization_id=org.id,
            domain_id=domain.id,
            report_id=str(uuid.uuid4()),
            org_name="google.com",
            date_range_begin=now - timedelta(days=1),
            date_range_end=now,
            policy_published_domain=domain.name,
            policy_p="quarantine",
            received_at=now,
        )
        db.add(report)
        await db.flush()
        record = DmarcAggregateRecord(
            organization_id=org.id,
            report_id=report.id,
            domain_id=domain.id,
            source_ip=source_ip,
            count=count,
            disposition=disposition,
            dkim_result=dkim_result,
            spf_result=spf_result,
            header_from=domain.name,
            auth_results={},
            created_at=now,
        )
        db.add(record)
        await db.commit()


async def test_action_queue_empty_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    assert response.json() == []


async def test_action_queue_scoped_to_one_domain(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue", params={"domain_id": str(domain.id)})

    assert response.status_code == 200


async def test_action_queue_unknown_domain_404s(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)

    response = await client.get("/api/action-queue", params={"domain_id": "00000000-0000-0000-0000-000000000000"})

    assert response.status_code == 404


async def test_action_queue_flags_unreviewed_senders_across_multiple_domains(api):
    """Regression coverage for the N+1 fix: unknown_sender_above_threshold
    used to be driven by a per-domain service_breakdown call inside the
    router's loop; now it's driven by service_breakdown_multi's batched
    result, keyed back out per domain. This proves that re-keying didn't
    lose or cross-wire any domain's data."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain_a = await _add_domain(owner_factory, org, name="a.example.com")
    domain_b = await _add_domain(owner_factory, org, name="b.example.com")
    await _add_sender(owner_factory, org, domain_a, source_ip="203.0.113.10", count=60)
    await _add_sender(owner_factory, org, domain_b, source_ip="203.0.113.20", count=75)

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    unreviewed = {i["domain_id"]: i for i in response.json() if "unreviewed sender" in i["title"]}
    assert set(unreviewed.keys()) == {str(domain_a.id), str(domain_b.id)}
    assert "60" in unreviewed[str(domain_a.id)]["title"]
    assert "75" in unreviewed[str(domain_b.id)]["title"]


async def test_action_queue_items_link_to_their_specific_subject_not_the_domain_page(api):
    """Regression coverage for the "everything links to the general domain
    page" finding: an unreviewed-sender item must deep-link to that exact
    sender on the Senders tab, not just /domains/{id}."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)
    domain = await _add_domain(owner_factory, org)
    await _add_sender(owner_factory, org, domain, source_ip="203.0.113.10", count=60)

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    item = next(i for i in response.json() if "unreviewed sender" in i["title"])
    assert item["link_path"] == f"/domains/{domain.id}/senders?highlight=203.0.113.10"


async def test_action_queue_low_compliance_cites_the_worst_check_finding_as_evidence(api):
    """Regression coverage for the "generic Fixes message" finding: a
    low-compliance item must cite the actual DNS check finding driving the
    score down (e.g. a specific SPF failure reason), not just the generic
    "Open DNS/authentication checks" hint alone."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org, verification_status=DomainVerificationStatus.verified)
    await login_as(client, owner_factory, user)

    # Mostly-failing traffic pushes the dmarc_pass_rate factor low, and the
    # failing SPF check pushes the spf factor to 0 — between them the score
    # drops below LOW_COMPLIANCE_SERIOUS_BELOW (70%).
    await _add_sender(
        owner_factory,
        org,
        domain,
        source_ip="203.0.113.50",
        count=100,
        disposition=Disposition.reject,
        dkim_result=AuthResult.fail,
        spf_result=AuthResult.fail,
    )
    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id,
                domain_id=domain.id,
                check_type=CheckType.spf,
                status=CheckStatus.fail,
                summary="Too many DNS lookups in SPF record (12/10) — senders may see a permerror",
                details={},
                checked_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    item = next(i for i in response.json() if "compliance" in i["title"])
    assert item["evidence"] == "Too many DNS lookups in SPF record (12/10) — senders may see a permerror"
    assert item["link_path"] == f"/domains/{domain.id}/dns"


async def test_action_queue_batches_service_breakdown_across_domains(api, monkeypatch):
    """The router should call service_breakdown_multi once for the whole
    request, not once per domain — this is the actual N+1 fix; the
    previous test only proves the output is still correct, not that the
    query count improved."""
    import app.routers.action_queue as action_queue_module

    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain_a = await _add_domain(owner_factory, org, name="a.example.com")
    domain_b = await _add_domain(owner_factory, org, name="b.example.com")
    await _add_sender(owner_factory, org, domain_a, source_ip="203.0.113.10", count=60)
    await _add_sender(owner_factory, org, domain_b, source_ip="203.0.113.20", count=75)

    calls = []
    real_service_breakdown_multi = action_queue_module.service_breakdown_multi

    async def _spy(db, domain_ids, **kwargs):
        calls.append(list(domain_ids))
        return await real_service_breakdown_multi(db, domain_ids, **kwargs)

    monkeypatch.setattr(action_queue_module, "service_breakdown_multi", _spy)

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    assert len(calls) == 1
    assert set(calls[0]) == {domain_a.id, domain_b.id}


async def test_action_queue_fetches_reviewed_labels_once_per_domain(api, monkeypatch):
    """unknown_sender_above_threshold and likely_spoofed_sender used to each
    independently call reviewed_service_labels, double-querying it per
    domain. The router now fetches it once per domain and shares it."""
    import app.routers.action_queue as action_queue_module

    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    await login_as(client, owner_factory, user)
    domain_a = await _add_domain(owner_factory, org, name="a.example.com")
    domain_b = await _add_domain(owner_factory, org, name="b.example.com")
    await _add_sender(owner_factory, org, domain_a, source_ip="203.0.113.10", count=60)
    await _add_sender(owner_factory, org, domain_b, source_ip="203.0.113.20", count=75)

    calls = []
    real_reviewed_service_labels = action_queue_module.reviewed_service_labels

    async def _spy(db, domain_id):
        calls.append(domain_id)
        return await real_reviewed_service_labels(db, domain_id)

    monkeypatch.setattr(action_queue_module, "reviewed_service_labels", _spy)

    response = await client.get("/api/action-queue")

    assert response.status_code == 200
    assert sorted(calls) == sorted([domain_a.id, domain_b.id])
