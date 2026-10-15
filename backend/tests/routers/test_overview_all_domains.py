"""The Overview's "All domains" counts the organization's verified domains
only, the same list as its domain picker: not reports for domains that
aren't added (they can share the mailbox) or aren't verified yet."""

from datetime import datetime, timezone

from app.models.enums import AuthResult, DomainVerificationStatus, UserRole

from tests.conftest import login_as, seed_org_and_user
from tests.routers.test_dmarc_reports import _add_aggregate_record, _add_aggregate_report, _add_domain, _fast_ip_fallback  # noqa: F401


async def test_all_domains_means_the_verified_domains(api):
    client, owner_factory = api
    org, user = await seed_org_and_user(owner_factory, role=UserRole.org_admin)
    await login_as(client, owner_factory, user)

    verified = await _add_domain(
        owner_factory, org, name="davids.online",
        verification_status=DomainVerificationStatus.verified, verified_at=datetime.now(timezone.utc),
    )
    pending = await _add_domain(owner_factory, org, name="pending.example")
    report = await _add_aggregate_report(owner_factory, org, verified)
    await _add_aggregate_record(owner_factory, org, verified, report, count=10, source_ip="203.0.113.1")
    await _add_aggregate_record(
        owner_factory, org, verified, report, count=2, source_ip="203.0.113.2",
        spf_result=AuthResult.fail, dkim_result=AuthResult.fail,
    )
    for domain, ip in ((pending, "203.0.113.3"), (None, "203.0.113.4")):  # unverified, and not added at all
        other = await _add_aggregate_report(owner_factory, org, domain)
        await _add_aggregate_record(
            owner_factory, org, domain, other, count=5, source_ip=ip,
            spf_result=AuthResult.fail, dkim_result=AuthResult.fail,
        )

    everything = (await client.get("/api/dmarc/posture", params={"days": 90})).json()
    one = (await client.get("/api/dmarc/posture", params={"days": 90, "domain_id": str(verified.id)})).json()

    assert everything["failed_volume"] == one["failed_volume"] == 2
    assert everything["compliance_pct"] == one["compliance_pct"]

    trend = (await client.get("/api/dmarc/trend", params={"days": 90})).json()
    assert sum(day["total"] for day in trend) == 12
