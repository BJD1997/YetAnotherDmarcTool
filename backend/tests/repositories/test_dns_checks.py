# backend/tests/repositories/test_dns_checks.py
from datetime import datetime, timezone

from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import CheckStatus, CheckType
from app.repositories.dns_checks import latest_dns_check_results_of_type_for_domain, list_dns_check_results_at_latest_run

from tests.conftest import seed_org_and_user


async def _add_domain(owner_factory, org, *, name: str = "example.com") -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name=name)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_dns_check_results_at_latest_run_groups_by_check_type_and_ignores_stale_runs(api):
    """Only rows from the most recent checked_at survive, and they come back
    grouped into a dict keyed by check_type — a check_type that produced
    several findings in the latest run (e.g. SPF's lookup-count finding plus
    its 'all'-qualifier finding) must keep every one of those rows, not
    collapse them."""
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc)
    latest = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.fail,
                summary="stale finding", checked_at=stale,
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.pass_,
                summary="lookup count ok", checked_at=latest,
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.warn,
                summary="all qualifier soft", checked_at=latest,
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.dmarc, status=CheckStatus.pass_,
                summary="dmarc record present", checked_at=latest,
            )
        )
        await db.commit()

    async with owner_factory() as db:
        findings_by_type = await list_dns_check_results_at_latest_run(db, domain.id)

    assert set(findings_by_type.keys()) == {CheckType.spf, CheckType.dmarc}
    assert {row.summary for row in findings_by_type[CheckType.spf]} == {"lookup count ok", "all qualifier soft"}
    assert [row.summary for row in findings_by_type[CheckType.dmarc]] == ["dmarc record present"]
    assert all(row.checked_at == latest for rows in findings_by_type.values() for row in rows)


async def test_list_dns_check_results_at_latest_run_empty_for_domain_with_no_checks(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)

    async with owner_factory() as db:
        findings_by_type = await list_dns_check_results_at_latest_run(db, domain.id)

    assert findings_by_type == {}


async def test_latest_dns_check_results_of_type_for_domain_only_returns_requested_type(api):
    """Two check_types seeded at the same checked_at (same recheck run) —
    requesting one type must not leak rows from the other, unlike
    list_dns_check_results_at_latest_run which deliberately returns every
    type at once."""
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    latest = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.warn,
                summary="spf near limit", checked_at=latest, details={"lookup_count": 9, "limit": 10},
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.dmarc, status=CheckStatus.pass_,
                summary="dmarc record present", checked_at=latest,
            )
        )
        await db.commit()

    async with owner_factory() as db:
        rows = await latest_dns_check_results_of_type_for_domain(db, domain.id, CheckType.spf)

    assert [row.summary for row in rows] == ["spf near limit"]
    assert all(row.check_type == CheckType.spf for row in rows)


async def test_latest_dns_check_results_of_type_for_domain_ignores_stale_runs(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc)
    latest = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.fail,
                summary="stale finding", checked_at=stale,
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.pass_,
                summary="fresh finding", checked_at=latest,
            )
        )
        await db.commit()

    async with owner_factory() as db:
        rows = await latest_dns_check_results_of_type_for_domain(db, domain.id, CheckType.spf)

    assert [row.summary for row in rows] == ["fresh finding"]


async def test_latest_dns_check_results_of_type_for_domain_empty_when_type_absent(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    latest = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.dmarc, status=CheckStatus.pass_,
                summary="dmarc record present", checked_at=latest,
            )
        )
        await db.commit()

    async with owner_factory() as db:
        rows = await latest_dns_check_results_of_type_for_domain(db, domain.id, CheckType.spf)

    assert list(rows) == []
