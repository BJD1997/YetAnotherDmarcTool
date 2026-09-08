from datetime import datetime, timezone

from app.models.enums import SourceMatchMethod
from app.repositories.source_identification import get_cached_identities, upsert_resolved_identities


async def test_get_cached_identities_empty_ips_returns_empty_dict(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        result = await get_cached_identities(db, [])
    assert result == {}


async def test_upsert_then_get_round_trips(api):
    # 192.0.2.0/24 (TEST-NET-1) deliberately, not 203.0.113.0/24: several
    # other test files (tests/routers/test_dmarc_reports.py,
    # tests/repositories/test_dmarc_reports_analytics.py) hardcode
    # 203.0.113.10 as a generic source_ip and rely on it resolving as an
    # ip_fallback identity via identify_many for the lifetime of the test
    # session — source_ip_identities is NOT truncated between tests (it's
    # deliberately not RLS-scoped, see the model's own docstring), so an
    # upsert here with a real service_label would leak into and break those
    # other tests. Picking an unused documentation range sidesteps the
    # collision entirely rather than relying on cleanup ordering.
    _client, owner_factory = api
    now = datetime.now(timezone.utc)
    rows = [
        {
            "source_ip": "192.0.2.10",
            "ptr_hostname": "mail.example.com",
            "service_label": "Example Mail",
            "match_method": SourceMatchMethod.ptr_domain.value,
            "fcrdns_valid": True,
            "resolved_at": now,
        }
    ]
    async with owner_factory() as db:
        await upsert_resolved_identities(db, rows)
        await db.commit()

    async with owner_factory() as db:
        result = await get_cached_identities(db, ["192.0.2.10"])

    assert "192.0.2.10" in result
    assert result["192.0.2.10"].service_label == "Example Mail"


async def test_upsert_on_conflict_updates_existing_row(api):
    _client, owner_factory = api
    now = datetime.now(timezone.utc)
    first = [
        {
            "source_ip": "192.0.2.20",
            "ptr_hostname": "old.example.com",
            "service_label": "Old Label",
            "match_method": SourceMatchMethod.ptr_domain.value,
            "fcrdns_valid": True,
            "resolved_at": now,
        }
    ]
    second = [{**first[0], "service_label": "New Label"}]
    async with owner_factory() as db:
        await upsert_resolved_identities(db, first)
        await db.commit()
    async with owner_factory() as db:
        await upsert_resolved_identities(db, second)
        await db.commit()

    async with owner_factory() as db:
        result = await get_cached_identities(db, ["192.0.2.20"])

    assert result["192.0.2.20"].service_label == "New Label"
