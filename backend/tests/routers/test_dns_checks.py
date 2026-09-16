from datetime import datetime, timedelta, timezone

from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import CheckStatus, CheckType, TlsRptPolicyType, UserRole
from app.models.tls_rpt import TlsRptReport

from tests.conftest import login_as, seed_org_and_user


async def _add_domain(owner_factory, org, **kwargs) -> Domain:
    async with owner_factory() as db:
        domain = Domain(organization_id=org.id, name="example.com", **kwargs)
        db.add(domain)
        await db.flush()
        await db.refresh(domain)
        await db.commit()
        return domain


async def test_list_latest_checks_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_latest_checks_returns_latest_run_only(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    async with owner_factory() as db:
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.fail,
                summary="stale finding", checked_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )
        db.add(
            DnsCheckResult(
                organization_id=org.id, domain_id=domain.id, check_type=CheckType.spf, status=CheckStatus.pass_,
                summary="current finding", checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["summary"] == "current finding"


async def test_list_latest_checks_not_found_for_other_org(api):
    client, owner_factory = api
    _org, user = await seed_org_and_user(owner_factory)
    # entra=True on the discarded second user avoids colliding with the
    # partial unique index on lower(email) for auth_method='local' users —
    # seed_org_and_user hardcodes the same email for every call (see Task 4).
    other_org, _other_user = await seed_org_and_user(owner_factory, entra=True)
    domain = await _add_domain(owner_factory, other_org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/checks")

    assert response.status_code == 404


async def test_tls_rpt_summary_empty(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_reports"] == 0
    assert body["failure_rate_pct"] is None


async def test_tls_rpt_reports_with_filters(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    # Seed multiple TlsRptReport rows with varying org_name and summary_failure_count
    async with owner_factory() as db:
        # Report 1: old date, org1, with failures
        db.add(
            TlsRptReport(
                organization_id=org.id,
                domain_id=domain.id,
                org_name="sender1.com",
                policy_domain=domain.name,
                policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=100),
                date_range_end=now - timedelta(days=99),
                summary_success_count=10,
                summary_failure_count=5,
                failure_details=[{"result_type": "certificate-expired", "failed_session_count": 3}],
                received_at=now - timedelta(days=99),
                created_at=now - timedelta(days=99),
            )
        )
        # Report 2: recent date, org1, no failures
        db.add(
            TlsRptReport(
                organization_id=org.id,
                domain_id=domain.id,
                org_name="sender1.com",
                policy_domain=domain.name,
                policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=5),
                date_range_end=now - timedelta(days=4),
                summary_success_count=20,
                summary_failure_count=0,
                failure_details=[],
                received_at=now - timedelta(days=4),
                created_at=now - timedelta(days=4),
            )
        )
        # Report 3: recent date, org2, with failures
        db.add(
            TlsRptReport(
                organization_id=org.id,
                domain_id=domain.id,
                org_name="sender2.com",
                policy_domain=domain.name,
                policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=3),
                date_range_end=now - timedelta(days=2),
                summary_success_count=15,
                summary_failure_count=7,
                failure_details=[{"result_type": "certificate-host-mismatch", "failed_session_count": 7}],
                received_at=now - timedelta(days=2),
                created_at=now - timedelta(days=2),
            )
        )
        await db.commit()

    await login_as(client, owner_factory, user)

    # Test: all reports
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports")
    assert response.status_code == 200
    body = response.json()["reports"]
    assert len(body) == 3

    # Test: filter by date range (last 10 days)
    date_from = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?date_from={date_from}")
    assert response.status_code == 200
    body = response.json()["reports"]
    assert len(body) == 2  # Only reports from last 10 days
    assert all(b["org_name"] in ["sender1.com", "sender2.com"] for b in body)

    # Test: filter by org_name
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?org_name=sender1")
    assert response.status_code == 200
    body = response.json()["reports"]
    assert len(body) == 2  # Both sender1.com reports
    assert all(b["org_name"] == "sender1.com" for b in body)

    # Test: filter by failures_only
    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?failures_only=true")
    assert response.status_code == 200
    body = response.json()["reports"]
    assert len(body) == 2  # Only reports with failures (org1 old + org2 recent)
    assert all(b["failed_session_count"] > 0 for b in body)

    # Test: combined filters (date range + org_name + failures_only)
    response = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?date_from={date_from}&org_name=sender2&failures_only=true"
    )
    assert response.status_code == 200
    body = response.json()["reports"]
    assert len(body) == 1
    assert body[0]["org_name"] == "sender2.com"
    assert body[0]["failed_session_count"] == 7


async def test_tls_rpt_reports_arbitrary_date_range_narrows_results(api):
    """A genuinely arbitrary date_from/date_to pair (not one of the old
    7/30/90-day presets) should narrow results to exactly the reports
    whose date_range_begin falls in [date_from 00:00 UTC, date_to+1
    00:00 UTC) — the whole point of replacing the days= preset filter."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    too_old = now - timedelta(days=30)
    in_range_older = now - timedelta(days=20)
    in_range_newer = now - timedelta(days=15)
    too_recent = now - timedelta(days=3)

    async with owner_factory() as db:
        for label, begin in [
            ("too_old", too_old),
            ("in_range_older", in_range_older),
            ("in_range_newer", in_range_newer),
            ("too_recent", too_recent),
        ]:
            db.add(
                TlsRptReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    org_name=f"{label}.com",
                    policy_domain=domain.name,
                    policy_type=TlsRptPolicyType.tlsa,
                    date_range_begin=begin,
                    date_range_end=begin + timedelta(days=1),
                    summary_success_count=10,
                    summary_failure_count=0,
                    failure_details=[],
                    received_at=begin,
                    created_at=begin,
                )
            )
        await db.commit()
    await login_as(client, owner_factory, user)

    # Wide margins (>=2 days) around both boundaries so the YYYY-MM-DD
    # truncation _parse_date_range does can't flip an assertion regardless
    # of what time of day this test happens to run at.
    date_from = (now - timedelta(days=22)).strftime("%Y-%m-%d")
    date_to = (now - timedelta(days=13)).strftime("%Y-%m-%d")

    response = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?date_from={date_from}&date_to={date_to}"
    )
    assert response.status_code == 200
    names = {r["org_name"] for r in response.json()["reports"]}
    assert names == {"in_range_older.com", "in_range_newer.com"}

    # /summary and /by-sender share the same date-range vocabulary.
    summary = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/summary?date_from={date_from}&date_to={date_to}"
    )
    assert summary.status_code == 200
    assert summary.json()["total_reports"] == 2

    by_sender = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/by-sender?date_from={date_from}&date_to={date_to}"
    )
    assert by_sender.status_code == 200
    assert {s["org_name"] for s in by_sender.json()} == {"in_range_older.com", "in_range_newer.com"}


async def test_tls_rpt_reports_date_to_boundary_is_inclusive_of_whole_day(api):
    """Same boundary precision check as the DMARC side (see
    test_dmarc_reports.py's test_dmarc_reports_by_day_date_to_boundary_is_
    inclusive_of_whole_day): `until` must be `date_to + 1 day` at 00:00
    UTC, not `date_to` itself. A report exactly AT midnight of date_to is
    INCLUDED; one exactly at midnight the day AFTER date_to is EXCLUDED.
    dns_checks.py's _parse_date_range is a separate (deliberately
    duplicated) implementation from dmarc_reports.py's, so it needs its
    own boundary coverage rather than relying on the DMARC test."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)

    on_boundary = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)  # exactly date_to's midnight
    past_boundary = datetime(2026, 6, 16, 0, 0, 0, tzinfo=timezone.utc)  # exactly the day after

    async with owner_factory() as db:
        for label, begin in [("on-boundary.com", on_boundary), ("past-boundary.com", past_boundary)]:
            db.add(
                TlsRptReport(
                    organization_id=org.id, domain_id=domain.id, org_name=label,
                    policy_domain=domain.name, policy_type=TlsRptPolicyType.tlsa,
                    date_range_begin=begin, date_range_end=begin + timedelta(days=1),
                    summary_success_count=5, summary_failure_count=0, failure_details=[],
                    received_at=begin, created_at=begin,
                )
            )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/reports?date_from=2026-05-01&date_to=2026-06-15"
    )
    assert response.status_code == 200
    names = {r["org_name"] for r in response.json()["reports"]}
    assert names == {"on-boundary.com"}  # the day-after report must be excluded


async def test_tls_rpt_reports_paginated(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        for i in range(3):
            db.add(
                TlsRptReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    org_name=f"sender{i}.com",
                    policy_domain=domain.name,
                    policy_type=TlsRptPolicyType.tlsa,
                    date_range_begin=now - timedelta(days=10 - i),
                    date_range_end=now - timedelta(days=9 - i),
                    summary_success_count=10,
                    summary_failure_count=0,
                    failure_details=[],
                    received_at=now - timedelta(days=9 - i),
                    created_at=now - timedelta(days=9 - i),
                )
            )
        await db.commit()
    await login_as(client, owner_factory, user)

    page1 = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/reports", params={"limit": 2})
    assert page1.status_code == 200
    page1_body = page1.json()
    assert len(page1_body["reports"]) == 2
    assert page1_body["has_more"] is True
    assert [r["org_name"] for r in page1_body["reports"]] == ["sender2.com", "sender1.com"]

    last_id = page1_body["reports"][-1]["id"]
    page2 = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/reports", params={"limit": 2, "before_id": last_id}
    )
    assert page2.status_code == 200
    page2_body = page2.json()
    assert [r["org_name"] for r in page2_body["reports"]] == ["sender0.com"]
    assert page2_body["has_more"] is False


async def test_tls_rpt_reports_result_type_filter_survives_all_filtered_page(api):
    """Regression test for the Fix 1 critical bug: result_type filters in
    Python after the SQL page comes back, so a SQL page whose rows are ALL
    filtered out must still return a cursor (next_before_id) derived from
    the SQL page itself, not from the (now-empty) visible rows — otherwise
    "Load more" dead-ends (has_more effectively becomes unreachable via the
    frontend's old row-derived cursor) even though matching history exists
    further back."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        # Two newest reports: neither has a "certificate-expired" entry in
        # failure_details — a SQL page with limit=2 fetching just these two
        # will have its post-filter `reports` list come back empty.
        db.add(
            TlsRptReport(
                organization_id=org.id, domain_id=domain.id, org_name="newest.com",
                policy_domain=domain.name, policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=1), date_range_end=now,
                summary_success_count=10, summary_failure_count=1,
                failure_details=[{"result_type": "certificate-host-mismatch", "failed_session_count": 1}],
                received_at=now, created_at=now,
            )
        )
        db.add(
            TlsRptReport(
                organization_id=org.id, domain_id=domain.id, org_name="second.com",
                policy_domain=domain.name, policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=2), date_range_end=now - timedelta(days=1),
                summary_success_count=10, summary_failure_count=0,
                failure_details=[],
                received_at=now - timedelta(days=1), created_at=now - timedelta(days=1),
            )
        )
        # Third-newest report DOES have a matching "certificate-expired"
        # entry — this is the row the frontend must still be able to reach
        # via "Load more" after the first (all-filtered-out) page.
        db.add(
            TlsRptReport(
                organization_id=org.id, domain_id=domain.id, org_name="third.com",
                policy_domain=domain.name, policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=3), date_range_end=now - timedelta(days=2),
                summary_success_count=10, summary_failure_count=2,
                failure_details=[{"result_type": "certificate-expired", "failed_session_count": 2}],
                received_at=now - timedelta(days=2), created_at=now - timedelta(days=2),
            )
        )
        # Fourth (oldest) report just fills out the second SQL page to
        # `limit` rows — not otherwise significant to the assertions.
        db.add(
            TlsRptReport(
                organization_id=org.id, domain_id=domain.id, org_name="fourth.com",
                policy_domain=domain.name, policy_type=TlsRptPolicyType.tlsa,
                date_range_begin=now - timedelta(days=4), date_range_end=now - timedelta(days=3),
                summary_success_count=10, summary_failure_count=0,
                failure_details=[],
                received_at=now - timedelta(days=3), created_at=now - timedelta(days=3),
            )
        )
        await db.commit()
    await login_as(client, owner_factory, user)

    # Page 1: the SQL page of size 2 returns [newest.com, second.com], and
    # neither matches result_type=certificate-expired, so `reports` comes
    # back empty. The old (broken) frontend derived its cursor from this
    # empty list, so "Load more" would silently disappear here even though
    # third.com — a real match — is still further back. The fix must keep
    # has_more True and return a real next_before_id anyway.
    page1 = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/reports",
        params={"limit": 2, "result_type": "certificate-expired"},
    )
    assert page1.status_code == 200
    page1_body = page1.json()
    assert page1_body["reports"] == []
    assert page1_body["has_more"] is True
    assert page1_body["next_before_id"] is not None

    # Page 2, paging on next_before_id (not on any visible row's id, since
    # there were none): the SQL page here is [third.com, fourth.com], and
    # third.com's matching failure_details entry must surface.
    page2 = await client.get(
        f"/api/domains/{domain.id}/dmarc/tls-rpt/reports",
        params={"limit": 2, "result_type": "certificate-expired", "before_id": page1_body["next_before_id"]},
    )
    assert page2.status_code == 200
    page2_body = page2.json()
    assert [r["org_name"] for r in page2_body["reports"]] == ["third.com"]


async def test_tls_rpt_summary_unaffected_by_pagination(api):
    """/summary must keep seeing the complete result set, not one page —
    this is the regression check for the split described in this task."""
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory)
    domain = await _add_domain(owner_factory, org)
    now = datetime.now(timezone.utc)

    async with owner_factory() as db:
        for i in range(3):
            db.add(
                TlsRptReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    org_name=f"sender{i}.com",
                    policy_domain=domain.name,
                    policy_type=TlsRptPolicyType.tlsa,
                    date_range_begin=now - timedelta(days=10 - i),
                    date_range_end=now - timedelta(days=9 - i),
                    summary_success_count=10,
                    summary_failure_count=0,
                    failure_details=[],
                    received_at=now - timedelta(days=9 - i),
                    created_at=now - timedelta(days=9 - i),
                )
            )
        await db.commit()
    await login_as(client, owner_factory, user)

    response = await client.get(f"/api/domains/{domain.id}/dmarc/tls-rpt/summary")
    assert response.status_code == 200
    assert response.json()["total_reports"] == 3


async def test_recheck_domain_requires_verified(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    domain = await _add_domain(owner_factory, org)
    await login_as(client, owner_factory, user)

    response = await client.post(f"/api/domains/{domain.id}/checks/recheck")

    assert response.status_code == 409
