"""Guards the one HTTP request this app makes to a customer-controlled
hostname — the MTA-STS policy fetch, https://mta-sts.<domain>/... (see
mta_sts.fetch_policy_file) — against reaching non-public addresses.

A domain only has to prove DNS control to be "verified" here, so an org admin
could point mta-sts.<their-domain> at 169.254.169.254 (cloud metadata),
127.0.0.1, or an internal 10.x/172.28.x host and turn the policy fetch into an
SSRF probe from inside the API container. This resolves the host up front and
refuses if any resolved address is private/loopback/link-local/reserved.

Residual (accepted for this authenticated, org-admin threat model): a DNS
rebind between this check and httpx's own resolution could still slip a private
IP through. Fully closing that needs connect-time IP pinning; this blocks the
straightforward cases without that complexity.
"""

import asyncio
import ipaddress
import socket


class BlockedAddressError(Exception):
    """Raised when a host resolves to a non-public address (or can't be
    resolved at all) — callers surface it as a fetch failure, not a crash."""


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    # Unwrap IPv4-mapped IPv6 (::ffff:10.0.0.1) so the v4 rules apply.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def assert_public_host(host: str) -> None:
    """Resolve `host` and raise BlockedAddressError unless every resolved
    address is public. Uses getaddrinfo (the same resolver httpx will use to
    connect), off the event loop so it doesn't block."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedAddressError(f"could not resolve {host}: {exc}") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise BlockedAddressError(f"no addresses resolved for {host}")
    for ip in addresses:
        if not _is_public(ip):
            raise BlockedAddressError(f"{host} resolves to a non-public address ({ip}) — refusing to fetch")
