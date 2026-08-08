"""Pivots the existing per-check dns_check_results rows (SPF/DKIM/DMARC/MX/
MTA-STS/DANE/STARTTLS — see registry.py) into a per-MX-host view, for the
Overview page's inbound-email table. No new checker logic beyond what
registry.py already runs; this is presentation only."""

import dataclasses
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dns_check import DnsCheckResult
from app.models.enums import CheckType
from app.services.source_identification.patterns import match_known_service
from app.services.source_identification.registrable_domain import registrable_domain


@dataclasses.dataclass
class InboundHostRow:
    host: str
    priority: int | None
    provider_label: str
    mx_status: str | None
    starttls_status: str | None
    dane_status: str | None
    mta_sts_status: str  # "pass" | "not_covered" | "not_configured"


async def build_inbound_hosts(db: AsyncSession, domain_id: uuid.UUID) -> list[InboundHostRow]:
    latest_ts = (
        select(func.max(DnsCheckResult.checked_at)).where(DnsCheckResult.domain_id == domain_id).scalar_subquery()
    )
    result = await db.execute(
        select(DnsCheckResult).where(DnsCheckResult.domain_id == domain_id, DnsCheckResult.checked_at == latest_ts)
    )
    rows = result.scalars().all()
    if not rows:
        return []

    priority_by_host: dict[str, int] = {}
    mx_status: dict[str, str] = {}
    dane_status: dict[str, str] = {}
    starttls_status: dict[str, str] = {}
    has_mta_sts_policy = False
    uncovered_hosts: set[str] = set()
    all_hosts: list[str] = []

    for row in rows:
        if row.check_type == CheckType.mx:
            if row.subject is None:
                for record in (row.details or {}).get("records", []):
                    priority_by_host[record["exchange"]] = record["preference"]
            else:
                mx_status[row.subject] = row.status.value
                if row.subject not in all_hosts:
                    all_hosts.append(row.subject)
        elif row.check_type == CheckType.dane and row.subject:
            dane_status[row.subject] = row.status.value
        elif row.check_type == CheckType.starttls and row.subject:
            starttls_status[row.subject] = row.status.value
        elif row.check_type == CheckType.mta_sts:
            if (row.details or {}).get("mx_patterns") is not None:
                has_mta_sts_policy = True
            uncovered = (row.details or {}).get("uncovered_hosts")
            if uncovered:
                uncovered_hosts.update(uncovered)

    rows_out = []
    for host in all_hosts:
        provider = match_known_service(host) or registrable_domain(host)
        if not has_mta_sts_policy:
            mta_sts = "not_configured"
        elif host in uncovered_hosts:
            mta_sts = "not_covered"
        else:
            mta_sts = "pass"

        rows_out.append(
            InboundHostRow(
                host=host,
                priority=priority_by_host.get(host),
                provider_label=provider,
                mx_status=mx_status.get(host),
                starttls_status=starttls_status.get(host),
                dane_status=dane_status.get(host),
                mta_sts_status=mta_sts,
            )
        )

    rows_out.sort(key=lambda r: (r.priority if r.priority is not None else 999999, r.host))
    return rows_out
