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
