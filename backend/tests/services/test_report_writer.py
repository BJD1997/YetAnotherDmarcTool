"""report_writer: parsed reports become rows. Characterization tests, written
before the row mapping was split out of the write functions (#27)."""

from sqlalchemy import select

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus, TlsRptPolicyType
from app.models.tls_rpt import TlsRptReport
from app.services.ingestion import report_writer

from tests.conftest import seed_org_and_user


def _aggregate(report_id="r1"):
    return {
        "report_metadata": {
            "report_id": report_id, "org_name": "google.com", "org_email": "noreply@google.com",
            "begin_date": "2026-10-01 00:00:00", "end_date": "2026-10-01 23:59:59",
        },
        "policy_published": {"domain": "example.com", "p": "reject", "sp": "none", "pct": "100", "adkim": "r", "aspf": "r"},
        "records": [
            {
                "source": {"ip_address": "203.0.113.1"}, "count": 4,
                "policy_evaluated": {"disposition": "none", "dkim": "pass", "spf": "fail"},
                "identifiers": {"header_from": "example.com", "envelope_from": "bounce.example.com"},
                "auth_results": {"dkim": [{"domain": "example.com", "selector": "s1", "result": "pass"}]},
            },
            {
                "source": {"ip_address": "203.0.113.2"}, "count": 2,
                "policy_evaluated": {"disposition": "reject", "dkim": "fail", "spf": "fail"},
                "identifiers": {"header_from": "shop.example.com"},
            },
        ],
    }


async def _setup(owner_factory):
    org, _user = await seed_org_and_user(owner_factory)
    async with owner_factory() as db:
        parent = Domain(organization_id=org.id, name="example.com", verification_status=DomainVerificationStatus.verified)
        db.add(parent)
        await db.flush()
        shop = Domain(organization_id=org.id, name="shop.example.com", parent_domain_id=parent.id,
                      verification_status=DomainVerificationStatus.verified)
        db.add(shop)
        await db.commit()
    return org, parent, shop


async def test_aggregate_report_rows_and_per_record_domains(api):
    _client, owner_factory = api
    org, parent, shop = await _setup(owner_factory)
    async with owner_factory() as db:
        assert await report_writer.write_aggregate_report(db, org.id, _aggregate(), "msg-1", verified=True) is True
        assert await report_writer.write_aggregate_report(db, org.id, _aggregate(), "msg-1", verified=True) is False
        await db.commit()

    async with owner_factory() as db:
        [report] = (await db.execute(select(DmarcAggregateReport).where(DmarcAggregateReport.organization_id == org.id))).scalars().all()
        records = (await db.execute(select(DmarcAggregateRecord).where(DmarcAggregateRecord.report_id == report.id).order_by(DmarcAggregateRecord.count.desc()))).scalars().all()
    assert (report.domain_id, report.report_id, report.org_name, report.email) == (parent.id, "r1", "google.com", "noreply@google.com")
    assert (report.policy_p, report.policy_sp, report.policy_pct, report.policy_adkim, report.sender_verified) == ("reject", "none", 100, "r", True)
    assert report.date_range_begin.isoformat().startswith("2026-10-01T00:00:00")
    first, second = records
    assert (first.domain_id, str(first.source_ip), first.count, first.disposition, first.dkim_result, first.spf_result) == (
        parent.id, "203.0.113.1", 4, Disposition.none, AuthResult.pass_, AuthResult.fail
    )
    assert (first.envelope_from, first.auth_results["dkim"][0]["selector"], first.sender_verified) == ("bounce.example.com", "s1", True)
    assert (second.domain_id, second.header_from, second.auth_results, second.disposition) == (shop.id, "shop.example.com", {}, Disposition.reject)


async def test_tls_rpt_report_one_row_per_policy(api):
    _client, owner_factory = api
    org, parent, shop = await _setup(owner_factory)
    parsed = {
        "organization_name": "Google Inc.", "begin_date": "2026-10-01T00:00:00Z", "end_date": "2026-10-01T23:59:59Z",
        "policies": [
            {"policy_domain": "example.com", "policy_type": "sts", "policy_strings": ["version: STSv1"],
             "successful_session_count": 10, "failed_session_count": 1,
             "failure_details": [{"result_type": "certificate-expired", "failed_session_count": 1}]},
            {"policy_domain": "shop.example.com", "policy_type": "no-policy-found", "successful_session_count": 3},
        ],
    }
    async with owner_factory() as db:
        assert await report_writer.write_smtp_tls_report(db, org.id, parsed, "msg-2") == 2
        assert await report_writer.write_smtp_tls_report(db, org.id, parsed, "msg-2") == 0
        await db.commit()

    async with owner_factory() as db:
        rows = (await db.execute(select(TlsRptReport).where(TlsRptReport.organization_id == org.id).order_by(TlsRptReport.policy_domain))).scalars().all()
    first, second = rows
    assert (first.domain_id, first.policy_type, first.summary_success_count, first.summary_failure_count) == (parent.id, TlsRptPolicyType.sts, 10, 1)
    assert first.policy_string == {"policy_strings": ["version: STSv1"]} and first.failure_details[0]["result_type"] == "certificate-expired"
    assert (second.domain_id, second.policy_type, second.summary_failure_count, second.failure_details) == (shop.id, TlsRptPolicyType.no_policy_found, 0, None)
    assert first.date_range_end.isoformat().startswith("2026-10-01T23:59:59")


def test_row_mapping_needs_no_database():
    import uuid
    from datetime import datetime, timezone

    org, report_id = uuid.uuid4(), uuid.uuid4()
    parsed = _aggregate()
    parsed["policy_published"]["pct"] = ""
    report = report_writer.aggregate_report_row(org, None, parsed, "msg")
    assert (report.policy_pct, report.domain_id, report.sender_verified) == (None, None, None)

    record = report_writer.aggregate_record_row(org, report_id, None, parsed["records"][1], False, datetime.now(timezone.utc))
    assert (record.envelope_from, record.auth_results, record.policy_evaluated_reasons, record.sender_verified) == (None, {}, None, False)

    tls = report_writer.tls_rpt_row(
        org, None, {"organization_name": "x", "begin_date": "2026-10-01T00:00:00Z", "end_date": "2026-10-02T00:00:00Z"},
        {"policy_domain": "example.com", "policy_type": "tlsa"}, "msg",
    )
    assert (tls.summary_success_count, tls.summary_failure_count, tls.failure_details) == (0, 0, None)
