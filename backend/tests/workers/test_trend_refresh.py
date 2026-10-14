"""trend_refresh stores one trend row per domain and updates it in place.
Organization isolation: tests/test_rls.py checks every org-scoped table,
and the trend endpoint test checks another org's domain 404s."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.domain_trend import DomainTrend
from app.models.enums import AuthResult, Disposition, DomainVerificationStatus
from app.services.rating.trend_refresh import refresh_trends

from tests.conftest import seed_org_and_user


async def _seed_days(db, org, domain, today, days_ago: int, total: int, passed: int) -> None:
    begin = datetime.combine(today - timedelta(days=days_ago), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=1)
    report = DmarcAggregateReport(
        organization_id=org.id, domain_id=domain.id, report_id=str(uuid.uuid4()), org_name="google.com",
        date_range_begin=begin, date_range_end=begin + timedelta(hours=23), policy_published_domain=domain.name,
        policy_p="reject", received_at=begin, created_at=begin,
    )
    db.add(report)
    await db.flush()
    for count, result in ((passed, AuthResult.pass_), (total - passed, AuthResult.fail)):
        if count:
            db.add(
                DmarcAggregateRecord(
                    organization_id=org.id, report_id=report.id, domain_id=domain.id, source_ip="203.0.113.5",
                    count=count, disposition=Disposition.none, dkim_result=result, spf_result=result,
                    header_from=domain.name, auth_results={}, created_at=begin,
                )
            )


async def test_refresh_stores_and_updates_one_row_per_domain(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    today = datetime.now(timezone.utc).date()
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="trend.example", verification_status=DomainVerificationStatus.verified)
        db.add(domain)
        await db.flush()
        for days_ago in range(7, 35):
            await _seed_days(db, org, domain, today, days_ago, 400, 400)
        for days_ago in range(0, 7):
            await _seed_days(db, org, domain, today, days_ago, 400, 360 if days_ago < 5 else 400)
        await db.commit()

    async with owner_factory() as db:
        assert await refresh_trends(db, today) >= 1
        await refresh_trends(db, today)
        await db.commit()
        rows = (await db.execute(select(DomainTrend).where(DomainTrend.domain_id == domain.id))).scalars().all()
    assert [r.state for r in rows] == ["down"]
    assert rows[0].baseline_pass_pct == 100.0
