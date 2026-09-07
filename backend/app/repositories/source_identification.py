from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_ip_identity import SourceIpIdentity


async def get_cached_identities(db: AsyncSession, ips: list[str]) -> dict[str, SourceIpIdentity]:
    """Cache rows for any of `ips` already resolved, keyed by the bare IP
    string. source_ip is INET; comparing it against a Python str list needs
    some cast, but CAST(inet AS text) (unlike inet's default display
    format) includes a "/32" or "/128" netmask suffix and would never match
    a bare address string — confirmed directly against this table's real
    data. host(inet) returns the bare address as text with no such suffix.

    asyncpg round-trips INET columns as ipaddress.IPv4Address/IPv6Address,
    not str — normalize to str so lookups against the plain-string `ips`
    argument actually hit (an IPv4Address key would never equal a str key)."""
    if not ips:
        return {}
    rows = (
        (await db.execute(select(SourceIpIdentity).where(func.host(SourceIpIdentity.source_ip).in_(ips))))
        .scalars()
        .all()
    )
    return {str(row.source_ip): row for row in rows}


async def upsert_resolved_identities(db: AsyncSession, rows: list[dict]) -> None:
    """`rows` entries must have exactly the keys source_ip, ptr_hostname,
    service_label, match_method, fcrdns_valid, resolved_at — the caller
    (service_identifier.py's identify_many) builds this list from its own
    SourceIdentity dataclass instances; this function stays primitive
    (plain dicts, no service-layer types) so the repository layer never
    imports from app/services/."""
    if not rows:
        return
    stmt = pg_insert(SourceIpIdentity).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SourceIpIdentity.source_ip],
        set_={
            "ptr_hostname": stmt.excluded.ptr_hostname,
            "service_label": stmt.excluded.service_label,
            "match_method": stmt.excluded.match_method,
            "fcrdns_valid": stmt.excluded.fcrdns_valid,
            "resolved_at": stmt.excluded.resolved_at,
        },
    )
    await db.execute(stmt)
