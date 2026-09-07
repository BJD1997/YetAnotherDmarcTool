# backend/tests/repositories/test_dmarc_reports_analytics.py
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, SenderReviewStatus, SourceMatchMethod
from app.models.sender_review import SenderReview
from app.models.source_ip_identity import SourceIpIdentity
from app.repositories.dmarc_reports import (
    list_reviewed_service_labels_for_domain,
    per_source_ip_volume_breakdown,
    policy_p_by_day_since,
    windowed_totals_excluding_blocked,
)

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
    owner_factory,
    org,
    domain,
    *,
    source_ip: str,
    count: int,
    date_range_begin: datetime | None = None,
    dkim_result: AuthResult = AuthResult.pass_,
    spf_result: AuthResult = AuthResult.pass_,
    policy_p: str | None = None,
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
            policy_p=policy_p,
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
            dkim_result=dkim_result,
            spf_result=spf_result,
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


async def test_windowed_totals_excluding_blocked_excludes_blocked_sender_traffic(api):
    """A source_ip whose identified service is marked 'blocked' for this
    domain (via SenderReview + the global SourceIpIdentity cache) must not
    count towards total_count/pass_count at all — not even as failed
    volume. An unrelated, unblocked source_ip's mixed pass/fail traffic
    should count normally."""
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)

    # Unblocked source: 10 passing (spf pass, dkim fail — still a DMARC pass
    # since it's an OR), 5 failing (both fail).
    await _add_report_and_record(
        owner_factory, org, domain, source_ip="203.0.113.10", count=10,
        dkim_result=AuthResult.fail, spf_result=AuthResult.pass_,
    )
    await _add_report_and_record(
        owner_factory, org, domain, source_ip="203.0.113.10", count=5,
        dkim_result=AuthResult.fail, spf_result=AuthResult.fail,
    )
    # Blocked source: a large passing volume that must be excluded entirely.
    await _add_report_and_record(
        owner_factory, org, domain, source_ip="198.51.100.20", count=100,
        dkim_result=AuthResult.pass_, spf_result=AuthResult.pass_,
    )

    async with owner_factory() as db:
        # SourceIpIdentity is a global, non-RLS cache (see its docstring) not
        # scoped/truncated per-test, so other test files' real identify_many()
        # calls may already have a row for this IP — upsert like identify_many
        # itself does, rather than a plain insert that could collide.
        stmt = pg_insert(SourceIpIdentity).values(
            source_ip="198.51.100.20",
            service_label="Blocked ESP",
            match_method=SourceMatchMethod.ip_fallback,
            resolved_at=datetime.now(timezone.utc),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceIpIdentity.source_ip],
            set_={"service_label": stmt.excluded.service_label, "match_method": stmt.excluded.match_method},
        )
        await db.execute(stmt)
        db.add(
            SenderReview(
                organization_id=org.id,
                domain_id=domain.id,
                service_label="Blocked ESP",
                status=SenderReviewStatus.blocked,
            )
        )
        await db.commit()

    async with owner_factory() as db:
        total_count, pass_count = await windowed_totals_excluding_blocked(
            db, domain.id, datetime.now(timezone.utc) - timedelta(days=30)
        )

    assert total_count == 15
    assert pass_count == 10


async def test_windowed_totals_excluding_blocked_respects_since_window(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    old = datetime.now(timezone.utc) - timedelta(days=120)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    await _add_report_and_record(owner_factory, org, domain, source_ip="203.0.113.10", count=5, date_range_begin=old)
    await _add_report_and_record(owner_factory, org, domain, source_ip="198.51.100.20", count=3, date_range_begin=recent)

    async with owner_factory() as db:
        total_count, pass_count = await windowed_totals_excluding_blocked(
            db, domain.id, datetime.now(timezone.utc) - timedelta(days=90)
        )

    assert total_count == 3
    assert pass_count == 3


async def test_policy_p_by_day_since_returns_distinct_day_policy_pairs_newest_first(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)
    too_old = now - timedelta(days=120)
    older_day = now - timedelta(days=10)
    newer_day = now - timedelta(days=2)

    # Outside the window entirely — must not appear.
    await _add_report_and_record(
        owner_factory, org, domain, source_ip="203.0.113.1", count=1, date_range_begin=too_old, policy_p="none"
    )
    # Two distinct policies reported on the same older day.
    await _add_report_and_record(
        owner_factory, org, domain, source_ip="203.0.113.2", count=1, date_range_begin=older_day, policy_p="none"
    )
    await _add_report_and_record(
        owner_factory, org, domain, source_ip="203.0.113.3", count=1, date_range_begin=older_day, policy_p="quarantine"
    )
    # A single policy on the more recent day.
    await _add_report_and_record(
        owner_factory, org, domain, source_ip="203.0.113.4", count=1, date_range_begin=newer_day, policy_p="reject"
    )

    async with owner_factory() as db:
        rows = await policy_p_by_day_since(db, domain.id, now - timedelta(days=30))

    policies_by_day: dict = {}
    for day, policy in rows:
        policies_by_day.setdefault(day.date(), set()).add(policy)

    assert policies_by_day == {
        older_day.date(): {"none", "quarantine"},
        newer_day.date(): {"reject"},
    }
    # Newest-day-first ordering.
    assert list(rows)[0][0].date() == newer_day.date()


async def test_list_reviewed_service_labels_for_domain_only_includes_terminal_statuses(api):
    """approved/ignored/blocked all count as "reviewed"; pending does not,
    and neither does a service with no SenderReview row at all."""
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)

    async with owner_factory() as db:
        db.add(
            SenderReview(
                organization_id=org.id, domain_id=domain.id, service_label="Approved ESP",
                status=SenderReviewStatus.approved,
            )
        )
        db.add(
            SenderReview(
                organization_id=org.id, domain_id=domain.id, service_label="Ignored ESP",
                status=SenderReviewStatus.ignored,
            )
        )
        db.add(
            SenderReview(
                organization_id=org.id, domain_id=domain.id, service_label="Blocked ESP",
                status=SenderReviewStatus.blocked,
            )
        )
        db.add(
            SenderReview(
                organization_id=org.id, domain_id=domain.id, service_label="Pending ESP",
                status=SenderReviewStatus.pending,
            )
        )
        await db.commit()

    async with owner_factory() as db:
        labels = await list_reviewed_service_labels_for_domain(db, domain.id)

    assert labels == {"Approved ESP", "Ignored ESP", "Blocked ESP"}


async def test_list_reviewed_service_labels_for_domain_empty_for_domain_with_no_reviews(api):
    _client, owner_factory = api
    org, _user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)

    async with owner_factory() as db:
        labels = await list_reviewed_service_labels_for_domain(db, domain.id)

    assert labels == set()
