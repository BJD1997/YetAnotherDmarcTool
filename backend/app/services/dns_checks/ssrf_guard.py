"""Guards the connections this app makes to customer-controlled hostnames —
the MTA-STS policy fetch, https://mta-sts.<domain>/... (see
mta_sts.fetch_policy_file), and the STARTTLS probe of a domain's MX hosts on
port 25 (see starttls.py) — against reaching non-public addresses.

A domain only has to prove DNS control to be "verified" here, so an org admin
could point mta-sts.<their-domain> at 169.254.169.254 (cloud metadata),
127.0.0.1, or an internal 10.x/172.28.x host and turn the policy fetch into an
SSRF probe from inside the API container. This resolves the host up front and
refuses if any resolved address is private/loopback/link-local/reserved.

The STARTTLS probe connects to the exact address checked here
(resolve_public_address), so it can't be rebound. The MTA-STS fetch lets httpx
resolve again; a DNS rebind in between is still possible there, but that fetch
also requires a valid TLS certificate for mta-sts.<domain>, which an internal
service won't present.
"""

import asyncio
import ipaddress
import socket


class BlockedAddressError(Exception):
    """Raised when a host resolves to a non-public address (or can't be
    resolved at all) — callers surface it as a fetch failure, not a crash."""


_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


def _is_public(ip: str) -> bool:
    """Only a globally routable address counts: an allowlist, so ranges like
    carrier-grade NAT (100.64.0.0/10, which Tailscale uses) are refused too.
    IPv6 addresses that carry an IPv4 address (IPv4-mapped, NAT64, 6to4,
    Teredo) are judged by the IPv4 address inside."""
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address):
        if addr in _NAT64_LOCAL:
            return False
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr in _NAT64:
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        elif addr.sixtofour is not None:
            addr = addr.sixtofour
        elif addr.teredo is not None:
            addr = addr.teredo[1]
    return addr.is_global and not addr.is_multicast


async def _resolve_public(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedAddressError(f"could not resolve {host}: {exc}") from exc

    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise BlockedAddressError(f"no addresses resolved for {host}")
    for ip in addresses:
        if not _is_public(ip):
            raise BlockedAddressError(f"{host} resolves to a non-public address ({ip}) — refusing to connect")
    return addresses


async def assert_public_host(host: str) -> None:
    """Resolve `host` and raise BlockedAddressError unless every resolved
    address is public. Uses getaddrinfo (the same resolver httpx will use to
    connect), off the event loop so it doesn't block."""
    await _resolve_public(host, 443)


async def resolve_public_address(host: str, port: int) -> str:
    """Like assert_public_host, but returns an address to connect to — so the
    caller connects to exactly what was checked, not a fresh lookup."""
    return (await _resolve_public(host, port))[0]
