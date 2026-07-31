"""Orchestrates source_ip -> sending-service identification: cache-first
(source_ip_identities), then bounded-concurrency PTR resolution + pattern
matching for cache misses, within a wall-clock budget. See the module-level
docstring on app/models/source_ip_identity.py for why this table has no
RLS, and patterns.py/registrable_domain.py for the matching logic itself."""

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SourceMatchMethod
from app.models.source_ip_identity import SourceIpIdentity
from app.services.dns_checks.resolver import DnsLookupError, resolve_ptr
from app.services.source_identification.patterns import match_known_service
from app.services.source_identification.registrable_domain import registrable_domain

CACHE_FRESHNESS = timedelta(days=30)

# The shared resolver's own per-query timeout (resolver.py) is 5s — longer
# than identify_many's default 4s overall wall-clock budget. Without its own
# shorter timeout here, a single unresponsive reverse-DNS zone (confirmed in
# practice: some source IPs, often spoofing/spam infrastructure, genuinely
# have no working PTR delegation and time out rather than answering
# NXDOMAIN) would always get cancelled by the outer budget before finishing
# — meaning it never produces a cacheable result and re-pays the same ~5s
# penalty on every future call, forever. A shorter per-lookup timeout lets
# even a hopeless lookup finish (as an ip_fallback) and get cached, so it
# only ever costs this much once.
PER_LOOKUP_TIMEOUT = 2.5


@dataclasses.dataclass
class SourceIdentity:
    service_label: str
    match_method: SourceMatchMethod
    ptr_hostname: str | None = None


async def _resolve_one(ip: str, semaphore: asyncio.Semaphore) -> tuple[str, SourceIdentity]:
    async with semaphore:
        try:
            ptr_hostname = await asyncio.wait_for(resolve_ptr(ip), timeout=PER_LOOKUP_TIMEOUT)
        except (DnsLookupError, asyncio.TimeoutError):
            ptr_hostname = None

        if ptr_hostname is None:
            return ip, SourceIdentity(service_label=ip, match_method=SourceMatchMethod.ip_fallback)

        known = match_known_service(ptr_hostname)
        if known:
            return ip, SourceIdentity(service_label=known, match_method=SourceMatchMethod.pattern, ptr_hostname=ptr_hostname)

        return ip, SourceIdentity(
            service_label=registrable_domain(ptr_hostname),
            match_method=SourceMatchMethod.ptr_domain,
            ptr_hostname=ptr_hostname,
        )


async def identify_many(
    db: AsyncSession, ips: list[str], *, concurrency: int = 15, time_budget_seconds: float = 4.0
) -> dict[str, SourceIdentity]:
    """Resolves each of `ips` to a SourceIdentity, cache-first. Cache misses
    are resolved concurrently (bounded) within a wall-clock budget — a fully
    cold cache for a busy domain can have 100+ distinct IPs, and PTR lookups
    that hit real timeouts would otherwise stall the request for tens of
    seconds. Anything not resolved within budget comes back as an
    ip_fallback identity for THIS call only (not written to cache), so it
    gets retried — and likely lands in cache — on the next call."""
    if not ips:
        return {}

    unique_ips = list(dict.fromkeys(ips))
    results: dict[str, SourceIdentity] = {}

    # source_ip is INET; comparing it against a Python str list needs some
    # cast, but CAST(inet AS text) (unlike inet's default display format)
    # includes a "/32" or "/128" netmask suffix and would never match a bare
    # address string — confirmed directly against this table's real data.
    # host(inet) returns the bare address as text with no such suffix.
    cache_rows = (
        (
            await db.execute(
                select(SourceIpIdentity).where(func.host(SourceIpIdentity.source_ip).in_(unique_ips))
            )
        )
        .scalars()
        .all()
    )
    # asyncpg round-trips INET columns as ipaddress.IPv4Address/IPv6Address,
    # not str — normalize to str so lookups against the plain-string `ips`
    # argument actually hit (an IPv4Address key would never equal a str key).
    cache_by_ip = {str(row.source_ip): row for row in cache_rows}
    fresh_cutoff = datetime.now(timezone.utc) - CACHE_FRESHNESS

    misses: list[str] = []
    for ip in unique_ips:
        row = cache_by_ip.get(ip)
        if row is not None and row.resolved_at >= fresh_cutoff:
            results[ip] = SourceIdentity(
                service_label=row.service_label, match_method=row.match_method, ptr_hostname=row.ptr_hostname
            )
        else:
            misses.append(ip)

    if not misses:
        return results

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [asyncio.create_task(_resolve_one(ip, semaphore)) for ip in misses]
    done, pending = await asyncio.wait(tasks, timeout=time_budget_seconds)
    for task in pending:
        task.cancel()

    resolved_now: dict[str, SourceIdentity] = {}
    for task in done:
        ip, identity = task.result()
        resolved_now[ip] = identity
        results[ip] = identity

    for ip in misses:
        if ip not in resolved_now:
            results[ip] = SourceIdentity(service_label=ip, match_method=SourceMatchMethod.ip_fallback)

    if resolved_now:
        now = datetime.now(timezone.utc)
        stmt = pg_insert(SourceIpIdentity).values(
            [
                {
                    "source_ip": ip,
                    "ptr_hostname": identity.ptr_hostname,
                    "service_label": identity.service_label,
                    "match_method": identity.match_method.value,
                    "resolved_at": now,
                }
                for ip, identity in resolved_now.items()
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceIpIdentity.source_ip],
            set_={
                "ptr_hostname": stmt.excluded.ptr_hostname,
                "service_label": stmt.excluded.service_label,
                "match_method": stmt.excluded.match_method,
                "resolved_at": stmt.excluded.resolved_at,
            },
        )
        await db.execute(stmt)

    return results
