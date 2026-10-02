"""What the notifications are made from: domains seen in reports but not
added, and DKIM selectors that signed a domain's mail but aren't monitored.
Shared by GET /dmarc/detected-domains and the notifications_refresh job."""

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.repositories import dmarc_reports as dmarc_reports_repo
from app.repositories.selectors import known_selector_names_for_org
from app.services.dmarc_narrative import describe_alignment
from app.services.source_identification.service_identifier import identify_many

SELECTOR_WINDOW_DAYS = 30


async def detected_domain_items(db: AsyncSession, organization_id: uuid.UUID) -> list[dict]:
    """Distinct domain names seen in reports that didn't match anything
    registered — see GET /dmarc/detected-domains for the full rationale."""
    detected: dict[str, dict] = {}

    # Needed up front (not just for the suggested-parent annotation below):
    # match_domain's ancestor walk means a subdomain of an already-registered
    # domain always resolves to that ancestor's domain_id, never NULL — so a
    # domain_id IS NULL filter alone would never catch it. Filtering out
    # exact registered names instead (further down) is what actually surfaces
    # that case, e.g. a subdomain sending real mail whose parent is
    # registered but which itself never was, exactly the gap that left
    # a real customer subdomain invisible until its mail started bouncing.
    registered = await dmarc_reports_repo.registered_domains_by_name(db, organization_id)
    registered_names = set(registered.keys())

    dismissed_names = {n.lower() for n in await dmarc_reports_repo.dismissed_domain_names(db, organization_id)}
    registered_lower = {name.lower() for name in registered_names}

    # Report generators don't always lower-case domain names ("Shop.Example.com"),
    # so names are normalized before comparing, or a subdomain of a registered
    # domain shows up as an unrelated one and gets added without its parent.
    def _add(raw: str, report_count: int, message_volume: int) -> None:
        name = (raw or "").strip().lower().rstrip(".")
        if not name or name in registered_lower:
            return
        entry = detected.setdefault(name, {"report_count": 0, "message_volume": 0})
        entry["report_count"] += report_count
        entry["message_volume"] += int(message_volume)

    for name, report_count, message_volume in await dmarc_reports_repo.unmatched_aggregate_domain_counts(
        db, organization_id
    ):
        _add(name, report_count, message_volume)
    for name, report_count, message_volume in await dmarc_reports_repo.unmatched_record_header_from_counts(
        db, organization_id
    ):
        _add(name, report_count, message_volume)
    for name, report_count, message_volume in await dmarc_reports_repo.unmatched_tls_rpt_domain_counts(
        db, organization_id
    ):
        _add(name, report_count, message_volume)
    for name, report_count in await dmarc_reports_repo.unmatched_forensic_domain_counts(db, organization_id):
        _add(name, report_count, 0)

    for name in dismissed_names:
        detected.pop(name, None)

    apex_registered = await dmarc_reports_repo.apex_domains_by_name(db, organization_id)
    detected_names = set(detected.keys())

    items = []
    for name, stats in detected.items():
        suggested_parent_id: str | None = None
        suggested_parent_name: str | None = None
        relationship = "apex"

        # The parent is the registered top-level domain it falls under:
        # domains nest one level only, so a registered subdomain (say
        # web02.example.com for x.web02.example.com) can't be the parent.
        for reg_name, reg_id in sorted(apex_registered.items(), key=lambda kv: -len(kv[0])):
            if name.endswith(f".{reg_name}"):
                suggested_parent_id = str(reg_id)
                suggested_parent_name = reg_name
                relationship = "subdomain_of_registered"
                break

        if suggested_parent_id is None:
            for other in sorted(detected_names, key=len):
                if other != name and name.endswith(f".{other}"):
                    relationship = "subdomain_of_detected"
                    suggested_parent_name = other
                    break

        items.append(
            {
                "name": name,
                "report_count": stats["report_count"],
                "message_volume": stats["message_volume"],
                "relationship": relationship,
                "suggested_parent_id": suggested_parent_id,
                "suggested_parent_name": suggested_parent_name,
            }
        )

    # Apex-looking entries first, then shorter (more likely-apex) names first.
    items.sort(key=lambda x: (x["relationship"] != "apex", len(x["name"]), -x["report_count"]))
    return items


@dataclasses.dataclass(frozen=True)
class SelectorFinding:
    domain_id: uuid.UUID
    domain_name: str
    selector: str
    message_volume: int
    # The sender (service label) behind most of the mail signed with it.
    sender_label: str | None


async def new_selectors(db: AsyncSession, organization_id: uuid.UUID) -> list[SelectorFinding]:
    """DKIM selectors that signed aligned mail in the last
    SELECTOR_WINDOW_DAYS days but aren't monitored — the same rule as GET
    /domains/{id}/selectors/detected, one query for the whole org."""
    since = datetime.now(timezone.utc) - timedelta(days=SELECTOR_WINDOW_DAYS)
    rows = await dmarc_reports_repo.recent_dkim_selectors_for_org(db, organization_id, since)
    known = await known_selector_names_for_org(db, organization_id)
    domains = {
        # Verified domains only: until then a domain's reports could be anyone's mail.
        d.id: d
        for d in (
            await db.execute(
                select(Domain).where(
                    Domain.organization_id == organization_id,
                    Domain.verification_status == DomainVerificationStatus.verified,
                )
            )
        ).scalars()
    }

    # (domain_id, selector) -> {source_ip: volume}
    found: dict[tuple[uuid.UUID, str], dict[str, int]] = {}
    for domain_id, selector, dkim_domain, source_ip, volume in rows:
        domain = domains.get(domain_id)
        if domain is None or not selector or (domain_id, selector) in known:
            continue
        if describe_alignment(dkim_domain, domain.name) == "none":
            continue
        by_ip = found.setdefault((domain_id, selector), {})
        by_ip[str(source_ip)] = by_ip.get(str(source_ip), 0) + int(volume)

    top_ips = [max(by_ip, key=by_ip.get) for by_ip in found.values()]
    identities = await identify_many(db, top_ips) if top_ips else {}
    findings = []
    for (domain_id, selector), by_ip in found.items():
        top_ip = max(by_ip, key=by_ip.get)
        identity = identities.get(top_ip)
        findings.append(
            SelectorFinding(
                domain_id=domain_id,
                domain_name=domains[domain_id].name,
                selector=selector,
                message_volume=sum(by_ip.values()),
                sender_label=identity.service_label if identity else top_ip,
            )
        )
    return sorted(findings, key=lambda f: (f.domain_name, f.selector))
