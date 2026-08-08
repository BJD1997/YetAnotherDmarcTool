"""Turns a parsedmarc-parsed report dict (see parsedmarc_adapter.py) into
rows in our own schema. Every write function is idempotent — reprocessing
the same message (e.g. after a poll run that failed partway through and
retried, since Graph's delta query only gives a resume token per page, not
per message) is safe: a duplicate natural key just rolls back its own
SAVEPOINT and is reported as "already ingested" rather than aborting the
whole batch or erroring."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition, TlsRptPolicyType
from app.models.tls_rpt import TlsRptReport
from app.services.ingestion.domain_matcher import match_domain

logger = logging.getLogger(__name__)


def _parse_agg_datetime(value: str) -> datetime:
    # parsedmarc's aggregate begin_date/end_date and forensic arrival_date_utc
    # are both "YYYY-MM-DD HH:MM:SS" strings, already converted to UTC.
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


async def write_aggregate_report(
    db: AsyncSession, organization_id: uuid.UUID, parsed: dict, source_message_id: str
) -> bool:
    """Returns True if newly written, False if this exact report was already
    ingested (natural key: organization + org_name + report_id + published domain,
    per RFC 7489's own dedup guidance)."""
    metadata = parsed["report_metadata"]
    policy = parsed["policy_published"]
    domain_id = await match_domain(db, organization_id, policy["domain"])

    report = DmarcAggregateReport(
        organization_id=organization_id,
        domain_id=domain_id,
        report_id=metadata["report_id"],
        org_name=metadata["org_name"],
        email=metadata.get("org_email"),
        date_range_begin=_parse_agg_datetime(metadata["begin_date"]),
        date_range_end=_parse_agg_datetime(metadata["end_date"]),
        policy_published_domain=policy["domain"],
        policy_p=policy.get("p"),
        policy_sp=policy.get("sp"),
        policy_pct=int(policy["pct"]) if policy.get("pct") not in (None, "") else None,
        policy_adkim=policy.get("adkim"),
        policy_aspf=policy.get("aspf"),
        source_message_id=source_message_id,
        received_at=datetime.now(timezone.utc),
    )

    try:
        async with db.begin_nested():
            db.add(report)
            await db.flush()
    except IntegrityError:
        logger.debug("duplicate aggregate report %s from %s, skipping", metadata.get("report_id"), metadata.get("org_name"))
        return False

    # RFC 7489 §7.2 keeps policy_published/domain (report-level: whichever
    # domain's DMARC record the receiver's DNS walk actually found) and each
    # record's identifiers/header_from (the literal RFC5322.From of that one
    # message) deliberately separate — a single report legitimately bundles
    # records for an organizational domain and any number of its subdomains,
    # since subdomain mail is evaluated against the inherited parent policy.
    # Attributing every record to the report's domain_id (as if the whole
    # report were about one domain) silently folds subdomain traffic into
    # the parent's stats forever. Re-resolving per header_from — cached here
    # since most reports only have a handful of distinct values across many
    # source-IP records — is what match_domain's own "closest registered
    # ancestor" walk is for.
    header_from_domain_ids: dict[str, uuid.UUID | None] = {}
    now = datetime.now(timezone.utc)
    records = []
    for rec in parsed["records"]:
        header_from = rec["identifiers"]["header_from"]
        if header_from not in header_from_domain_ids:
            header_from_domain_ids[header_from] = await match_domain(db, organization_id, header_from)
        records.append(
            DmarcAggregateRecord(
                organization_id=organization_id,
                report_id=report.id,
                domain_id=header_from_domain_ids[header_from],
                source_ip=rec["source"]["ip_address"],
                count=rec["count"],
                disposition=Disposition(rec["policy_evaluated"]["disposition"]),
                dkim_result=AuthResult(rec["policy_evaluated"]["dkim"]),
                spf_result=AuthResult(rec["policy_evaluated"]["spf"]),
                header_from=header_from,
                envelope_from=rec["identifiers"].get("envelope_from"),
                envelope_to=rec["identifiers"].get("envelope_to"),
                auth_results=rec.get("auth_results") or {},
                policy_evaluated_reasons=rec["policy_evaluated"].get("policy_override_reasons") or None,
                created_at=now,
            )
        )
    db.add_all(records)
    await db.flush()
    return True


async def write_forensic_report(
    db: AsyncSession, organization_id: uuid.UUID, parsed: dict, source_message_id: str
) -> bool:
    reported_domain = parsed.get("reported_domain") or ""
    domain_id = await match_domain(db, organization_id, reported_domain) if reported_domain else None
    source = parsed.get("source") or {}

    report = DmarcForensicReport(
        organization_id=organization_id,
        domain_id=domain_id,
        arrival_date=_parse_agg_datetime(parsed["arrival_date_utc"]),
        source_ip=source.get("ip_address"),
        reported_domain=reported_domain,
        original_envelope_id=parsed.get("original_envelope_id"),
        dkim_domain=parsed.get("dkim_domain"),
        spf_dns=parsed.get("spf_dns"),
        authentication_results=parsed.get("authentication_results"),
        raw_message=parsed.get("sample"),
        source_message_id=source_message_id,
        created_at=datetime.now(timezone.utc),
    )
    try:
        async with db.begin_nested():
            db.add(report)
            await db.flush()
    except IntegrityError:
        logger.debug("duplicate forensic report for message %s, skipping", source_message_id)
        return False
    return True


def _parse_tls_rpt_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def write_smtp_tls_report(
    db: AsyncSession, organization_id: uuid.UUID, parsed: dict, source_message_id: str
) -> int:
    """A single TLS-RPT report can cover multiple policy domains — one row
    is written per policy entry. Returns the count of newly-written rows
    (policies already ingested are skipped, not counted)."""
    date_begin = _parse_tls_rpt_datetime(parsed["begin_date"])
    date_end = _parse_tls_rpt_datetime(parsed["end_date"])
    written = 0

    for policy in parsed.get("policies", []):
        policy_domain = policy["policy_domain"]
        domain_id = await match_domain(db, organization_id, policy_domain)

        report = TlsRptReport(
            organization_id=organization_id,
            domain_id=domain_id,
            org_name=parsed["organization_name"],
            date_range_begin=date_begin,
            date_range_end=date_end,
            policy_type=TlsRptPolicyType(policy["policy_type"]),
            policy_domain=policy_domain,
            policy_string={"policy_strings": policy.get("policy_strings")},
            summary_success_count=policy.get("successful_session_count") or 0,
            summary_failure_count=policy.get("failed_session_count") or 0,
            failure_details=policy.get("failure_details") or None,
            source_message_id=source_message_id,
            received_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        try:
            async with db.begin_nested():
                db.add(report)
                await db.flush()
            written += 1
        except IntegrityError:
            logger.debug("duplicate TLS-RPT policy %s from %s, skipping", policy_domain, parsed.get("organization_name"))

    return written


async def resweep_unmatched_reports(db: AsyncSession, organization_id: uuid.UUID) -> dict:
    """Re-runs match_domain() against every report currently sitting in the
    "unmatched" bucket (domain_id IS NULL) for this org, re-linking anything
    that now resolves — the same domain-registered-after-some-reports-already-
    arrived gap this function exists to close. Called right after a domain is
    created (see app/routers/domains.py); cheap enough to run synchronously
    since the unmatched bucket is self-limiting (only grows between when
    reports start arriving and when the relevant domain gets registered)."""
    counts = {"aggregate_reports": 0, "forensic_reports": 0, "tls_rpt_reports": 0}

    agg_result = await db.execute(
        select(DmarcAggregateReport).where(
            DmarcAggregateReport.organization_id == organization_id, DmarcAggregateReport.domain_id.is_(None)
        )
    )
    for report in agg_result.scalars().all():
        domain_id = await match_domain(db, organization_id, report.policy_published_domain)
        if domain_id is not None:
            report.domain_id = domain_id
            # Deliberately NOT blanket-copying this onto the report's
            # records anymore — a report's policy_published/domain and its
            # records' own header_from can legitimately differ (RFC 7489
            # §7.2, subdomain mail evaluated under an inherited parent
            # policy). resweep_domain_records (called alongside this for
            # the same newly-registered domain, see app/routers/domains.py)
            # re-matches records by their own header_from instead.
            counts["aggregate_reports"] += 1

    forensic_result = await db.execute(
        select(DmarcForensicReport).where(
            DmarcForensicReport.organization_id == organization_id, DmarcForensicReport.domain_id.is_(None)
        )
    )
    for report in forensic_result.scalars().all():
        if not report.reported_domain:
            continue
        domain_id = await match_domain(db, organization_id, report.reported_domain)
        if domain_id is not None:
            report.domain_id = domain_id
            counts["forensic_reports"] += 1

    tls_result = await db.execute(
        select(TlsRptReport).where(TlsRptReport.organization_id == organization_id, TlsRptReport.domain_id.is_(None))
    )
    for report in tls_result.scalars().all():
        domain_id = await match_domain(db, organization_id, report.policy_domain)
        if domain_id is not None:
            report.domain_id = domain_id
            counts["tls_rpt_reports"] += 1

    if any(counts.values()):
        await db.flush()
    return counts


async def resweep_domain_records(db: AsyncSession, organization_id: uuid.UUID, domain: Domain) -> int:
    """Re-attributes DmarcAggregateRecord rows by re-resolving match_domain()
    against each one's own header_from, scoped to exactly the header_from
    values this newly-registered domain could possibly affect: itself, or
    anything ending in ".{domain.name}" — nothing else is reachable by
    match_domain's ancestor walk now that this domain exists. Re-examines
    *existing* rows (not just domain_id IS NULL ones), so calling this for
    an already-registered domain doubles as a backfill using the exact same
    matching logic write_aggregate_report uses at ingestion time, rather
    than a separately-maintained one-off script. Called alongside
    resweep_unmatched_reports right after every domain is created (see
    app/routers/domains.py) — apex or subdomain, going forward or backfill,
    same call site."""
    candidates = (
        await db.execute(
            select(DmarcAggregateRecord.header_from)
            .where(
                DmarcAggregateRecord.organization_id == organization_id,
                or_(
                    DmarcAggregateRecord.header_from == domain.name,
                    DmarcAggregateRecord.header_from.like(f"%.{domain.name}"),
                ),
            )
            .distinct()
        )
    ).scalars().all()

    updated = 0
    for header_from in candidates:
        resolved_domain_id = await match_domain(db, organization_id, header_from)
        result = await db.execute(
            DmarcAggregateRecord.__table__.update()
            .where(
                DmarcAggregateRecord.organization_id == organization_id,
                DmarcAggregateRecord.header_from == header_from,
                DmarcAggregateRecord.domain_id.is_distinct_from(resolved_domain_id),
            )
            .values(domain_id=resolved_domain_id)
        )
        updated += result.rowcount

    if updated:
        await db.flush()
    return updated
