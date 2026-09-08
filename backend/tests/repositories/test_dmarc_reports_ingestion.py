# backend/tests/repositories/test_dmarc_reports_ingestion.py
import uuid
from datetime import datetime, timezone

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition
from app.repositories.dmarc_reports import (
    distinct_header_froms_for_domain_or_descendants,
    insert_aggregate_report_if_new,
    insert_forensic_report_if_new,
    list_unmatched_aggregate_reports,
    list_unmatched_forensic_reports_for_org,
    update_record_domain_id_for_header_from,
)

from tests.conftest import seed_org_and_user


def _agg_report(org_id, *, domain_id=None, report_id=None, org_name="reporter.example") -> DmarcAggregateReport:
    now = datetime.now(timezone.utc)
    return DmarcAggregateReport(
        organization_id=org_id, domain_id=domain_id, report_id=report_id or str(uuid.uuid4()),
        org_name=org_name, date_range_begin=now, date_range_end=now,
        policy_published_domain="example.com", received_at=now,
    )


async def test_insert_aggregate_report_if_new_rejects_duplicate(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    natural_key_report_id = "dup-test-1"

    async with owner_factory() as db:
        first = await insert_aggregate_report_if_new(db, _agg_report(org.id, report_id=natural_key_report_id))
        await db.commit()
    assert first is True

    async with owner_factory() as db:
        second = await insert_aggregate_report_if_new(db, _agg_report(org.id, report_id=natural_key_report_id))
        await db.commit()
    assert second is False


async def test_insert_forensic_report_if_new_rejects_duplicate(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)

    def _forensic(source_message_id):
        return DmarcForensicReport(
            organization_id=org.id, arrival_date=now, reported_domain="example.com",
            source_message_id=source_message_id, created_at=now,
        )

    async with owner_factory() as db:
        first = await insert_forensic_report_if_new(db, _forensic("msg-1"))
        await db.commit()
    assert first is True

    async with owner_factory() as db:
        second = await insert_forensic_report_if_new(db, _forensic("msg-1"))
        await db.commit()
    assert second is False


async def test_list_unmatched_aggregate_reports_no_limit_returns_all(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)

    async with owner_factory() as db:
        for i in range(3):
            await insert_aggregate_report_if_new(db, _agg_report(org.id, report_id=f"unmatched-{i}"))
        await db.commit()

    async with owner_factory() as db:
        results = await list_unmatched_aggregate_reports(db, org.id, limit=None)
    assert len(results) == 3


async def test_list_unmatched_forensic_reports_for_org(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        db.add(DmarcForensicReport(
            organization_id=org.id, domain_id=None, arrival_date=now,
            reported_domain="example.com", source_message_id="fx-1", created_at=now,
        ))
        await db.commit()

    async with owner_factory() as db:
        results = await list_unmatched_forensic_reports_for_org(db, org.id)
    assert len(results) == 1


async def test_distinct_header_froms_and_update_domain_id(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com")
        db.add(domain)
        await db.flush()
        report = _agg_report(org.id, report_id="hf-test")
        db.add(report)
        await db.flush()
        db.add(DmarcAggregateRecord(
            organization_id=org.id, report_id=report.id, domain_id=None, source_ip="203.0.113.5",
            count=1, disposition=Disposition.none, dkim_result=AuthResult.pass_, spf_result=AuthResult.pass_,
            header_from="mail.example.com", auth_results={}, created_at=datetime.now(timezone.utc),
        ))
        await db.commit()

    async with owner_factory() as db:
        candidates = await distinct_header_froms_for_domain_or_descendants(db, org.id, "example.com")
    assert "mail.example.com" in candidates

    async with owner_factory() as db:
        updated = await update_record_domain_id_for_header_from(db, org.id, "mail.example.com", domain.id)
        await db.commit()
    assert updated == 1
