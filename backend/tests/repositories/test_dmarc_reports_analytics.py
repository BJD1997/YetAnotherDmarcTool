# backend/tests/repositories/test_dmarc_reports_analytics.py
import uuid
from datetime import datetime, timedelta, timezone

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition
from app.repositories.dmarc_reports import per_source_ip_volume_breakdown

from tests.conftest import seed_org_and_user


async def _add_domain(owner_factory, org, *, name: str = "example.com") -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name=name)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def _add_report_and_record(
    owner_factory, org, domain, *, source_ip: str, count: int, date_range_begin: datetime | None = None
) -> None:
    now = datetime.now(timezone.utc)
    begin = date_range_begin or now - timedelta(days=1)
    async with owner_factory() as db:
        report = DmarcAggregateReport(
            organization_id=org.id,
            domain_id=domain.id,
            report_id=str(uuid.uuid4()),
            org_name="reporter.example",
            date_range_begin=begin,
            date_range_end=begin + timedelta(days=1),
            policy_published_domain=domain.name,
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
            disposition=Disposition.none,
            dkim_result=AuthResult.pass_,
            spf_result=AuthResult.pass_,
            header_from=domain.name,
            auth_results={},
            created_at=now,
        )
        db.add(record)
        await db.commit()


async def test_per_source_ip_volume_breakdown_groups_by_ip(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await _add_report_and_record(owner_factory, org, domain, source_ip="203.0.113.10", count=5)
    await _add_report_and_record(owner_factory, org, domain, source_ip="198.51.100.20", count=3)

    async with owner_factory() as db:
        rows = await per_source_ip_volume_breakdown(db, domain.id)

    by_ip = {str(r[0]): r for r in rows}
    assert by_ip["203.0.113.10"][1] == 5
    assert by_ip["198.51.100.20"][1] == 3


async def test_per_source_ip_volume_breakdown_since_filter_excludes_old_traffic(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    old = datetime.now(timezone.utc) - timedelta(days=90)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    await _add_report_and_record(owner_factory, org, domain, source_ip="203.0.113.10", count=5, date_range_begin=old)
    await _add_report_and_record(owner_factory, org, domain, source_ip="198.51.100.20", count=3, date_range_begin=recent)

    async with owner_factory() as db:
        rows = await per_source_ip_volume_breakdown(db, domain.id, since=datetime.now(timezone.utc) - timedelta(days=30))

    ips = {str(r[0]) for r in rows}
    assert ips == {"198.51.100.20"}
