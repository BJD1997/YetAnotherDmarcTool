"""Auto-provisions the RFC 7489 §7.1 external-destination authorization
record a client domain needs before receivers will honor rua=/ruf= pointing
at an operator-hosted address — the other side of the exact same mechanism
app/services/dns_checks/dmarc.py's _check_external_destination checks for.
Called from POST /domains/{id}/hosted-report-address every time (idempotent
— safe to retry), not just on first creation, so a transient Cloudflare
failure is self-healing on the next call rather than requiring new UI."""

import dataclasses
import logging
from typing import Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"


@dataclasses.dataclass
class ProvisionResult:
    status: Literal["created", "already_exists", "unconfigured", "error"]
    detail: str | None


def _manual_fallback_detail(client_domain: str, record_name: str, hosted_domain: str) -> str:
    return (
        f"On {hosted_domain}'s DNS (not {client_domain}'s) — publish a TXT record at {record_name} with the "
        f'value "v=DMARC1". This authorizes {hosted_domain} to receive DMARC reports on {client_domain}\'s behalf.'
    )


async def ensure_authorization_record(client_domain: str) -> ProvisionResult:
    hosted_domain = settings.hosted_reports_address_domain
    if not hosted_domain:
        return ProvisionResult(status="unconfigured", detail=None)

    record_name = f"{client_domain}._report._dmarc.{hosted_domain}"

    if not settings.cloudflare_api_token or not settings.cloudflare_zone_id:
        return ProvisionResult(
            status="unconfigured", detail=_manual_fallback_detail(client_domain, record_name, hosted_domain)
        )

    headers = {"Authorization": f"Bearer {settings.cloudflare_api_token}"}
    zone_id = settings.cloudflare_zone_id

    try:
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            existing = await client.get(
                f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records", params={"type": "TXT", "name": record_name}
            )
            existing_body = existing.json()
            if existing.status_code != 200 or not existing_body.get("success"):
                return ProvisionResult(status="error", detail=_cf_error(existing_body))

            # Cloudflare stores/returns TXT content with the surrounding
            # quote characters included when a record was created via its
            # dashboard (confirmed live against the operator's own
            # hand-created records) — strip('"') so this still recognizes
            # an existing record regardless of which path created it.
            if any(r.get("content", "").strip().strip('"').startswith("v=DMARC1") for r in existing_body.get("result", [])):
                return ProvisionResult(status="already_exists", detail=None)

            created = await client.post(
                f"{CLOUDFLARE_API_BASE}/zones/{zone_id}/dns_records",
                json={"type": "TXT", "name": record_name, "content": '"v=DMARC1"'},
            )
            created_body = created.json()
            if created.status_code not in (200, 201) or not created_body.get("success"):
                return ProvisionResult(status="error", detail=_cf_error(created_body))

            return ProvisionResult(status="created", detail=None)
    except httpx.HTTPError as exc:
        logger.warning("Cloudflare provisioning failed for %s: %s", record_name, exc)
        return ProvisionResult(status="error", detail=str(exc))


def _cf_error(body: dict) -> str:
    errors = body.get("errors") or []
    if errors:
        return "; ".join(e.get("message", str(e)) for e in errors)
    return "Cloudflare API request failed"
