"""Shared DNS lookup helper, used by domain verification and every Phase 3
checker (SPF/DKIM/DMARC/MX now; MTA-STS/DANE in Phase 4). All lookups route
through the `resolver` container (mvance/unbound, DNSSEC-validating — see
docker-compose.yml and resolver/zz-access-control.conf) rather than whatever
resolver the container's /etc/resolv.conf points at, so DANE's DNSSEC-chain
requirement can eventually be satisfied by trusting the response's AD flag."""

import socket

import dns.asyncresolver
import dns.exception
import dns.resolver

from app.config import settings

_resolver: dns.asyncresolver.Resolver | None = None


class DnsLookupError(Exception):
    """Transient/infra failure (timeout, SERVFAIL, no reachable nameserver) —
    distinct from a definitive negative answer (NXDOMAIN/NoAnswer, which just
    means "this record doesn't exist"). Checkers need this distinction to
    report status=error ("we couldn't determine this") rather than
    status=fail ("we determined it's non-compliant") when DNS itself is
    flaky rather than the domain's configuration being wrong."""


def _get_resolver() -> dns.asyncresolver.Resolver:
    global _resolver
    if _resolver is None:
        resolver_ip = socket.gethostbyname(settings.dns_resolver_host)
        r = dns.asyncresolver.Resolver(configure=False)
        r.nameservers = [resolver_ip]
        r.timeout = 5
        r.lifetime = 5
        _resolver = r
    return _resolver


async def resolve_txt(name: str) -> list[str]:
    """Decoded value of each TXT record at `name`, or [] on NXDOMAIN/no
    answer/lookup error alike — used where "couldn't check" and "doesn't
    exist" are treated the same (e.g. domain ownership verification, where
    the customer just retries either way)."""
    try:
        return await resolve_txt_strict(name)
    except DnsLookupError:
        return []


async def resolve_txt_strict(name: str) -> list[str]:
    resolver = _get_resolver()
    try:
        answer = await resolver.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise DnsLookupError(f"TXT lookup for {name} failed: {exc}") from exc
    return [b"".join(rdata.strings).decode("utf-8", errors="replace") for rdata in answer]


async def resolve_mx(name: str) -> list[tuple[int, str]]:
    """[(preference, exchange_hostname), ...] sorted by preference, or []
    if the domain has no MX records (NXDOMAIN/NoAnswer)."""
    resolver = _get_resolver()
    try:
        answer = await resolver.resolve(name, "MX")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise DnsLookupError(f"MX lookup for {name} failed: {exc}") from exc
    records = sorted((rdata.preference, str(rdata.exchange).rstrip(".")) for rdata in answer)
    return records


async def resolve_cname(name: str) -> str | None:
    """The CNAME target at `name`, or None if there isn't one."""
    resolver = _get_resolver()
    try:
        answer = await resolver.resolve(name, "CNAME")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        raise DnsLookupError(f"CNAME lookup for {name} failed: {exc}") from exc
    return str(answer[0].target).rstrip(".")


async def resolve_address(name: str) -> list[str]:
    """Combined A + AAAA addresses for `name`, or [] if neither exists."""
    resolver = _get_resolver()
    addresses: list[str] = []
    saw_any_answer = False
    for rdtype in ("A", "AAAA"):
        try:
            answer = await resolver.resolve(name, rdtype)
            addresses.extend(str(rdata) for rdata in answer)
            saw_any_answer = True
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            continue
        except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
            if not saw_any_answer and rdtype == "AAAA":
                # Only escalate if we got nothing useful at all — a lone
                # AAAA timeout after a successful A lookup isn't fatal.
                raise DnsLookupError(f"Address lookup for {name} failed: {exc}") from exc
    return addresses
