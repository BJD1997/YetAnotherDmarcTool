"""Orchestrates source_ip -> sending-service identification: cache-first
(source_ip_identities), then bounded-concurrency PTR resolution + pattern
matching for cache misses, within a wall-clock budget. See the module-level
docstring on app/models/source_ip_identity.py for why this table has no
RLS, and patterns.py/registrable_domain.py for the matching logic itself."""

import asyncio
import dataclasses
import ipaddress
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SourceMatchMethod
from app.repositories.source_identification import get_cached_identities, upsert_resolved_identities
from app.services.dns_checks.resolver import DnsLookupError, resolve_address, resolve_ptr
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
    # Forward-confirmed reverse DNS: None when there's no PTR to confirm
    # (ip_fallback), else whether ptr_hostname resolves forward back to the IP.
    fcrdns_valid: bool | None = None


async def _forward_confirm(ip: str, ptr_hostname: str) -> bool | None:
    """Whether ptr_hostname's A/AAAA records include `ip` — the "forward
    confirm" half of FCrDNS. A PTR alone is trivially spoofable/stale (the IP's
    owner controls it unilaterally); only a matching forward record proves the
    hostname genuinely claims the IP too.

    Returns True (forward record points back), False (the hostname resolves but
    to other/no addresses — a definitive FCrDNS failure), or None when the
    forward lookup itself couldn't be completed (timeout/SERVFAIL). None is
    deliberately NOT cached as a failure: identify_many re-resolves a row whose
    fcrdns_valid is NULL, so a transient blip is retried next time rather than
    freezing a false "rDNS fail" onto a legitimate sender for the cache TTL."""
    try:
        addresses = await asyncio.wait_for(resolve_address(ptr_hostname), timeout=PER_LOOKUP_TIMEOUT)
    except (DnsLookupError, asyncio.TimeoutError):
        return None
    target = ipaddress.ip_address(ip)
    for addr in addresses:
        try:
            if ipaddress.ip_address(addr) == target:
                return True
        except ValueError:
            continue
    return False


async def _resolve_one(ip: str, semaphore: asyncio.Semaphore) -> tuple[str, SourceIdentity]:
    async with semaphore:
        try:
            ptr_hostname = await asyncio.wait_for(resolve_ptr(ip), timeout=PER_LOOKUP_TIMEOUT)
        except (DnsLookupError, asyncio.TimeoutError):
            ptr_hostname = None

        if ptr_hostname is None:
            return ip, SourceIdentity(service_label=ip, match_method=SourceMatchMethod.ip_fallback)

        fcrdns_valid = await _forward_confirm(ip, ptr_hostname)
        known = match_known_service(ptr_hostname)
        if known:
            return ip, SourceIdentity(
                service_label=known,
                match_method=SourceMatchMethod.pattern,
                ptr_hostname=ptr_hostname,
                fcrdns_valid=fcrdns_valid,
            )

        return ip, SourceIdentity(
            service_label=registrable_domain(ptr_hostname),
            match_method=SourceMatchMethod.ptr_domain,
            ptr_hostname=ptr_hostname,
            fcrdns_valid=fcrdns_valid,
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

    cache_by_ip = await get_cached_identities(db, unique_ips)
    fresh_cutoff = datetime.now(timezone.utc) - CACHE_FRESHNESS

    misses: list[str] = []
    for ip in unique_ips:
        row = cache_by_ip.get(ip)
        # A row with a ptr_hostname but no fcrdns_valid predates this column
        # (or was seeded without it) — re-resolve so it gets forward-confirmed,
        # rather than doing DNS from a migration. Rows with no ptr_hostname
        # (ip_fallback) legitimately have NULL fcrdns_valid and are left alone.
        needs_fcrdns = row is not None and row.ptr_hostname is not None and row.fcrdns_valid is None
        if row is not None and row.resolved_at >= fresh_cutoff and not needs_fcrdns:
            results[ip] = SourceIdentity(
                service_label=row.service_label,
                match_method=row.match_method,
                ptr_hostname=row.ptr_hostname,
                fcrdns_valid=row.fcrdns_valid,
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
        rows = [
            {
                "source_ip": ip,
                "ptr_hostname": identity.ptr_hostname,
                "service_label": identity.service_label,
                "match_method": identity.match_method.value,
                "fcrdns_valid": identity.fcrdns_valid,
                "resolved_at": now,
            }
            for ip, identity in resolved_now.items()
        ]
        await upsert_resolved_identities(db, rows)

    return results
