"""Generates human-readable verdict prose for a single DMARC aggregate
record, from data already captured at ingestion (no new columns, no new
parsing) — the Identifiers/Authentication/Verdict sections of the report
detail view. Pure functions, no I/O.

auth_results' real shape, confirmed against live production data (not
assumed from parsedmarc's docs): {"spf": [{"scope", "domain", "result"}],
"dkim": [{"domain", "result", "selector"}]} — both lists, DKIM commonly has
0/1/2 entries (multi-signature emails are common)."""

from typing import Any

_SPF_SCOPE_LABELS = {"mfrom": "rfc5321.MailFrom", "helo": "rfc5321.HELO"}


def describe_alignment(auth_domain: str | None, header_from: str) -> str:
    """"strict" (exact match), "relaxed" (same simplified base domain — see
    the same organizational-domain approximation already accepted in
    dmarc.py's _check_external_destination), or "none"."""
    if not auth_domain:
        return "none"
    a = auth_domain.rstrip(".").lower()
    h = header_from.rstrip(".").lower()
    if a == h:
        return "strict"
    if a.endswith(f".{h}") or h.endswith(f".{a}"):
        return "relaxed"
    return "none"


def spf_narratives(auth_results: dict[str, Any], source_ip: str, header_from: str) -> list[str]:
    narratives = []
    for entry in auth_results.get("spf") or []:
        domain = entry.get("domain") or "(unknown)"
        result = entry.get("result", "unknown")
        scope_label = _SPF_SCOPE_LABELS.get(entry.get("scope"), "envelope")
        alignment = describe_alignment(domain, header_from)
        if result == "pass":
            if alignment != "none":
                narratives.append(
                    f"The SPF record of {scope_label} address {domain} designates {source_ip} as a permitted "
                    f"sender (DMARC aligned, {alignment})."
                )
            else:
                narratives.append(
                    f"The SPF record of {scope_label} address {domain} designates {source_ip} as a permitted "
                    f"sender, but this domain doesn't align with the message's From address ({header_from})."
                )
        else:
            narratives.append(f"The SPF check for {scope_label} address {domain} did not pass (result: {result}).")
    return narratives


def dkim_narratives(auth_results: dict[str, Any], header_from: str) -> list[str]:
    narratives = []
    for entry in auth_results.get("dkim") or []:
        domain = entry.get("domain") or "(unknown)"
        selector = entry.get("selector") or "(unknown)"
        result = entry.get("result", "unknown")
        alignment = describe_alignment(domain, header_from)
        record_name = f"{selector}._domainkey.{domain}"
        if result == "pass":
            if alignment != "none":
                narratives.append(
                    f"The email was signed with a DKIM signature that matched the DKIM record published at "
                    f"{record_name} (DMARC aligned, {alignment})."
                )
            else:
                narratives.append(
                    f"The email was signed with a valid DKIM signature at {record_name}, but this domain doesn't "
                    f"align with the message's From address ({header_from})."
                )
        else:
            narratives.append(
                f"The sender signed the email with DKIM, but the receiver could not verify the signature against "
                f"the DKIM record published at {record_name}."
            )
    return narratives
