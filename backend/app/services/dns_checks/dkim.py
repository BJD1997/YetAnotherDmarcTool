"""DKIM (RFC 6376) selector checker. Selectors aren't discoverable via DNS
alone, so they're registered manually per domain (see the DkimSelector model
and app/routers/domains.py's selector endpoints) — this checks each
registered selector's published key record."""

import base64

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key

from app.models.enums import DomainMailProfile
from app.services.dns_checks.base import Finding
from app.services.dns_checks.resolver import DnsLookupError, resolve_txt_strict

MIN_RSA_KEY_SIZE_WARN = 2048
MIN_RSA_KEY_SIZE_FAIL = 1024


def _parse_tags(record: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in record.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        tags[key.strip().lower()] = value.strip()
    return tags


async def check_selector(domain: str, selector: str) -> Finding:
    name = f"{selector}._domainkey.{domain}"
    try:
        records = await resolve_txt_strict(name)
    except DnsLookupError as exc:
        return Finding(status="error", summary=f"DKIM lookup failed for selector {selector}: {exc}", subject=selector)

    if not records:
        return Finding(status="fail", summary=f"No DKIM record found at {name}", subject=selector)

    # Multiple separate TXT RRs at the same selector name (not to be
    # confused with one RR's value being split across multiple DNS strings,
    # which resolve_txt_strict already concatenates) is itself invalid.
    extra_note = None
    if len(records) > 1:
        extra_note = f"{len(records)} separate TXT records found at {name} (should be exactly one)"

    tags = _parse_tags(records[0])

    if tags.get("v", "DKIM1").upper() != "DKIM1":
        return Finding(status="fail", summary=f"Unexpected DKIM version tag: {tags.get('v')!r}", subject=selector)

    if "p" not in tags:
        return Finding(status="fail", summary=f"No public key (p=) tag for selector {selector}", subject=selector)
    if tags["p"] == "":
        return Finding(status="fail", summary=f"Selector {selector}'s key is revoked (empty p= tag)", subject=selector)

    key_type = tags.get("k", "rsa").lower()
    details: dict = {"key_type": key_type, "test_mode": tags.get("t", "").lower() == "y"}

    try:
        public_key = load_der_public_key(base64.b64decode(tags["p"]))
    except Exception as exc:
        return Finding(
            status="fail", summary=f"Could not parse public key for selector {selector}: {exc}", subject=selector, details=details
        )

    status = "pass"
    messages = [f"DKIM selector {selector} OK"]
    if extra_note:
        status = "warn"
        messages.append(extra_note)

    if isinstance(public_key, rsa.RSAPublicKey):
        details["key_size_bits"] = public_key.key_size
        if public_key.key_size < MIN_RSA_KEY_SIZE_FAIL:
            status = "fail"
            messages.append(f"RSA key is only {public_key.key_size} bits (minimum {MIN_RSA_KEY_SIZE_FAIL})")
            details["recommendation"] = f"Rotate to a {MIN_RSA_KEY_SIZE_WARN}-bit (or larger) DKIM key as soon as possible."
        elif public_key.key_size < MIN_RSA_KEY_SIZE_WARN:
            status = "warn" if status == "pass" else status
            messages.append(f"RSA key is {public_key.key_size} bits — {MIN_RSA_KEY_SIZE_WARN}+ recommended")
            details["recommendation"] = f"Rotate or upgrade to a {MIN_RSA_KEY_SIZE_WARN}-bit (or larger) DKIM key."
        else:
            messages.append(f"RSA {public_key.key_size}-bit key")

    if details["test_mode"]:
        status = "warn" if status == "pass" else status
        messages.append("selector is in test mode (t=y) — some receivers won't enforce DKIM failures while this is set")
        details.setdefault("recommendation", "Remove t=y once you've confirmed this selector signs correctly.")

    return Finding(status=status, summary="; ".join(messages), subject=selector, details=details)


async def check(domain: str, selectors: list[str], mail_profile: DomainMailProfile = DomainMailProfile.sends_mail) -> list[Finding]:
    if not selectors:
        # DKIM signs outbound mail — a domain that never sends any (see
        # Domain.mail_profile) has nothing to sign, so "no selectors" isn't
        # a gap to flag, unlike a domain that's expected to be sending.
        if mail_profile != DomainMailProfile.sends_mail:
            return [Finding(status="pass", summary="No DKIM selectors registered — expected, this domain doesn't send mail")]
        return [
            Finding(
                status="warn",
                summary="No DKIM selectors registered — add one to check DKIM (selectors aren't discoverable via DNS alone)",
            )
        ]
    return [await check_selector(domain, selector) for selector in selectors]
