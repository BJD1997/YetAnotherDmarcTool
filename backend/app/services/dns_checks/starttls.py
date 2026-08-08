"""Live STARTTLS (RFC 3207) probe for SMTP: connects to each MX host on
port 25, checks whether STARTTLS is advertised in the EHLO response, and —
critically — actually completes the TLS handshake rather than trusting the
advertisement alone (a server can claim STARTTLS support in EHLO and still
fail to negotiate it, which matters for real deliverability and isn't
caught by reading capabilities). Unlike the other checkers in this package,
this isn't a DNS lookup at all — same category as mta_sts.py's live HTTPS
fetch.

TLS certificate verification is deliberately DISABLED for this probe.
Opportunistic MTA-to-MTA STARTTLS is explicitly "encrypt if possible, don't
require a valid cert" by design — real MX hosts routinely present certs
that wouldn't pass strict validation but still provide meaningful
encryption against passive eavesdropping. Authenticated TLS is what
DANE/MTA-STS are for, and both are already checked separately (dane.py,
mta_sts.py); this checker's only job is "does opportunistic encryption
work at all"."""

import asyncio
import ssl

from app.services.dns_checks.base import Finding, is_null_mx
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx

SMTP_PORT = 25
CONNECT_TIMEOUT = 10.0
_CRLF = b"\r\n"


async def _read_smtp_response(reader: asyncio.StreamReader) -> tuple[int, list[str]]:
    lines = []
    code = 0
    while True:
        raw = await asyncio.wait_for(reader.readline(), timeout=CONNECT_TIMEOUT)
        if not raw:
            raise ConnectionError("connection closed while reading SMTP response")
        line = raw.decode("ascii", errors="replace").rstrip("\r\n")
        code = int(line[:3])
        lines.append(line[4:] if len(line) > 4 else "")
        if len(line) < 4 or line[3] != "-":
            break
    return code, lines


async def _probe_host(host: str) -> Finding:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, SMTP_PORT), timeout=CONNECT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as exc:
        return Finding(status="error", summary=f"Could not connect to {host}:{SMTP_PORT}: {exc}", subject=host)

    try:
        code, _ = await _read_smtp_response(reader)
        if code != 220:
            return Finding(status="error", summary=f"{host} did not send a 220 greeting on connect (got {code})", subject=host)

        writer.write(b"EHLO dmarcwatch-probe" + _CRLF)
        await writer.drain()
        code, lines = await _read_smtp_response(reader)
        if code != 250:
            return Finding(status="error", summary=f"{host} rejected EHLO (code {code})", subject=host)

        if not any(line.strip().upper() == "STARTTLS" for line in lines):
            return Finding(
                status="fail",
                summary=f"{host} does not advertise STARTTLS — inbound mail can be delivered in plaintext",
                subject=host,
            )

        writer.write(b"STARTTLS" + _CRLF)
        await writer.drain()
        code, _ = await _read_smtp_response(reader)
        if code != 220:
            return Finding(
                status="fail",
                summary=f"{host} advertises STARTTLS but refused the STARTTLS command (code {code})",
                subject=host,
            )

        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE  # opportunistic TLS — see module docstring
        try:
            await asyncio.wait_for(writer.start_tls(ssl_context), timeout=CONNECT_TIMEOUT)
        except (ssl.SSLError, asyncio.TimeoutError) as exc:
            return Finding(
                status="fail", summary=f"{host} advertises STARTTLS but the TLS handshake failed: {exc}", subject=host
            )

        return Finding(status="pass", summary=f"{host} supports STARTTLS and completed a TLS handshake", subject=host)
    finally:
        writer.close()


async def check(domain: str) -> list[Finding]:
    try:
        mx_records = await resolve_mx(domain)
    except DnsLookupError as exc:
        return [Finding(status="error", summary=f"Could not look up MX records for STARTTLS check: {exc}")]

    if not mx_records or is_null_mx(mx_records):
        return []  # no mail received here — mx.py already flags MX-level issues

    results = await asyncio.gather(*(_probe_host(host) for _preference, host in mx_records))
    return list(results)
