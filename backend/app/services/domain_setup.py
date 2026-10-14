"""Adding domains, on their own or as a family (a root domain with the
subdomains reports were already seen for), and removing one that was never
verified. Shared by POST /domains, POST /domains/detected and
DELETE /domains/{id} so report attribution works the same way for all three.

Domains nest one level: a root domain with subdomains under it. Verifying the
root proves control of its DNS, so its subdomains follow it: added under a
verified root they're verified at once, under a pending root they're verified
together with it (see mark_pending_subdomains_verified)."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from parsedmarc.utils import get_base_domain
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.enums import DomainMailProfile, DomainVerificationStatus
from app.repositories.domains import count_subdomains, list_domains_for_org
from app.services.ingestion.report_writer import resweep_domain_records, resweep_unmatched_reports
from app.services.notifications.sources import detected_domain_items


def root_domain_name(name: str) -> str:
    """The registrable domain a name falls under (web02.example.co.uk ->
    example.co.uk), or the name itself."""
    return (get_base_domain(name) or name).lower()


async def add_domain(
    db: AsyncSession,
    organization_id: uuid.UUID,
    name: str,
    *,
    parent: Domain | None = None,
    notes: str | None = None,
    mail_profile: DomainMailProfile | None = None,
) -> Domain:
    """Inserts the domain (flushes; an existing name raises IntegrityError).
    A subdomain of a verified root is verified straight away."""
    domain = Domain(
        organization_id=organization_id,
        parent_domain_id=parent.id if parent is not None else None,
        name=name,
        notes=notes,
        mail_profile=mail_profile or DomainMailProfile.sends_mail,
    )
    if parent is not None and parent.verification_status == DomainVerificationStatus.verified:
        domain.verification_status = DomainVerificationStatus.verified
        domain.verified_at = datetime.now(timezone.utc)
    db.add(domain)
    await db.flush()
    return domain


async def attribute_reports(db: AsyncSession, organization_id: uuid.UUID, domain_name: str) -> dict:
    """Re-matches reports and records after a domain under `domain_name` was
    added or removed: waiting reports go to the newest, most specific match;
    a removed domain's reports go back to its parent, or to Detected domains."""
    counts = await resweep_unmatched_reports(db, organization_id)
    records = await resweep_domain_records(db, organization_id, SimpleNamespace(name=domain_name))
    return {"reports": counts["aggregate_reports"], "records": records}


async def add_detected_domain(db: AsyncSession, organization_id: uuid.UUID, name: str) -> dict:
    """Adds a domain from Detected domains together with its family:
    - a subdomain goes under its root domain: an added one, or a detected one
      that's added first (the root is then the one to verify); with neither,
      the subdomain is added on its own;
    - other detected subdomains of that root are added under it as well;
    - root-level domains that are really subdomains of it move under it,
      keeping their verification.
    Returns the names added and moved, root first."""
    name = name.strip().lower().rstrip(".")
    existing = {d.name: d for d in await list_domains_for_org(db, organization_id)}
    if name in existing:
        raise ValueError(f"{name} is already added")

    registered_roots = [d for d in existing.values() if d.parent_domain_id is None]
    root = next(
        (d for d in sorted(registered_roots, key=lambda d: -len(d.name)) if name.endswith(f".{d.name}")), None
    )
    detected = [item["name"] for item in await detected_domain_items(db, organization_id)]
    added: list[Domain] = []
    if root is None:
        root_name = root_domain_name(name)
        if root_name != name and (root_name not in detected or root_name in existing):
            # Its root isn't seen in reports (or is added as someone's
            # subdomain): only offered as a family when it is, so this one
            # is added on its own.
            domain = await add_domain(db, organization_id, name)
            await attribute_reports(db, organization_id, domain.name)
            return {"added": [domain.name], "moved": [], "root": domain.name}
        root = await add_domain(db, organization_id, root_name)
        added.append(root)
    if name != root.name:
        added.append(await add_domain(db, organization_id, name, parent=root))

    for other in detected:
        if other.endswith(f".{root.name}") and other not in existing and all(d.name != other for d in added):
            added.append(await add_domain(db, organization_id, other, parent=root))

    moved: list[str] = []
    for other in registered_roots:
        if other.id == root.id or not other.name.endswith(f".{root.name}"):
            continue
        if await count_subdomains(db, other.id) > 0:
            continue  # it has subdomains of its own; nesting is one level only
        other.parent_domain_id = root.id
        if (
            root.verification_status == DomainVerificationStatus.verified
            and other.verification_status != DomainVerificationStatus.verified
        ):
            other.verification_status = DomainVerificationStatus.verified
            other.verified_at = datetime.now(timezone.utc)
        moved.append(other.name)
    await db.flush()

    for domain in added:
        await attribute_reports(db, organization_id, domain.name)
    return {"added": [d.name for d in added], "moved": moved, "root": root.name}
