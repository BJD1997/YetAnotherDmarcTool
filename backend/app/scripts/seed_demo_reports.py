"""Seeds synthetic DMARC aggregate reports for the demo org so the dashboard
shows a realistic, populated view — a spread of sending services, a policy that
moved to enforcement over time, some failing/spoofed traffic, and a subdomain
sending mail that was never registered (to exercise "detected domains") —
rather than the handful of real reports that trickle in for the demo's own
domain.

Demo-only: a no-op unless DEMO_LOGIN_EMAIL is set and the is_demo_read_only org
already exists (seed_demo.py runs first and creates it). Runs on every demo
migrate but is idempotent — reports are keyed by a deterministic natural key
per reporter+day over a trailing 90-day window, so re-running only tops up days
not already present (which also keeps the demo's data window fresh across
redeploys). Also pre-seeds source_ip_identities so the synthetic source IPs
resolve to friendly service labels without any live PTR lookup."""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.db.rls import set_org_context, set_platform_admin_context
from app.db.session import async_session_factory
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import AuthResult, Disposition
from app.models.organization import Organization
from app.models.source_ip_identity import SourceIpIdentity

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_demo_reports")

DEMO_DOMAIN = "yetanotherdmarctool.com"
WINDOW_DAYS = 90

# Receivers that generate the aggregate reports (the report's org_name).
REPORTERS = ["google.com", "Enterprise Outlook", "Yahoo! Inc.", "Mail.Ru", "Fastmail Pty Ltd"]

# Sending sources seen in the reports. outcome drives auth results:
#   aligned   -> SPF pass + DKIM pass  (DMARC pass)
#   dkim_only -> SPF fail + DKIM pass  (DMARC pass; e.g. forwarded / relayed)
#   spoof     -> SPF fail + DKIM fail  (DMARC fail)
# (ip, service_label|None, ptr|None, header_from, outcome, weight, (min_vol, max_vol))
SOURCES = [
    ("40.107.0.42", "Microsoft 365", "mail-0107.protection.outlook.com", DEMO_DOMAIN, "aligned", 40, (400, 3000)),
    ("209.85.220.41", "Google Workspace", "mail-209-85-220-41.google.com", DEMO_DOMAIN, "aligned", 18, (100, 900)),
    ("149.72.130.17", "SendGrid", "o1.ptr.sendgrid.net", DEMO_DOMAIN, "aligned", 14, (200, 1500)),
    ("54.240.11.30", "Amazon SES", "a11-30.smtp-out.amazonses.com", DEMO_DOMAIN, "aligned", 8, (50, 600)),
    # ESP sending as a marketing subdomain that was never registered -> "detected domains".
    ("149.72.140.9", "SendGrid", "o2.ptr.sendgrid.net", "newsletter." + DEMO_DOMAIN, "aligned", 9, (300, 1800)),
    # Forwarding: SPF breaks in transit, DKIM survives -> still DMARC pass.
    ("128.148.20.15", "brown.edu", "mail.brown.edu", DEMO_DOMAIN, "dkim_only", 5, (5, 60)),
    # Unauthorized / spoofing infrastructure -> DMARC fail (no PTR, shown as raw IP).
    ("45.137.21.88", None, None, DEMO_DOMAIN, "spoof", 6, (10, 220)),
    ("193.42.33.104", None, None, DEMO_DOMAIN, "spoof", 4, (5, 130)),
]
_MAX_WEIGHT = max(s[5] for s in SOURCES)


def _policy_for(days_ago: int) -> str:
    """Tell a 'moved to enforcement' story across the window."""
    if days_ago > 60:
        return "none"
    if days_ago > 25:
        return "quarantine"
    return "reject"


def _disposition(outcome: str, policy_p: str) -> Disposition:
    if outcome in ("aligned", "dkim_only"):
        return Disposition.none  # DMARC passed -> delivered regardless of policy
    return {"none": Disposition.none, "quarantine": Disposition.quarantine, "reject": Disposition.reject}[policy_p]


def _auth_results(header_from: str, outcome: str) -> dict:
    dkim_pass = outcome in ("aligned", "dkim_only")
    spf_pass = outcome == "aligned"
    return {
        "dkim": [{"domain": header_from if dkim_pass else "unaligned-sender.example", "selector": "selector1", "result": "pass" if dkim_pass else "fail"}],
        "spf": [{"domain": header_from if spf_pass else "bounce.mailer.example", "scope": "mfrom", "result": "pass" if spf_pass else "fail"}],
    }


def _weighted_subset(rng: random.Random) -> list[tuple]:
    chosen = [s for s in SOURCES if rng.random() < (s[5] / _MAX_WEIGHT)]
    if len(chosen) < 2:
        chosen = rng.sample(SOURCES, 3)
    return chosen


async def main() -> None:
    if not settings.demo_login_email:
        logger.info("DEMO_LOGIN_EMAIL not set; skipping demo report seed")
        return

    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        org = (
            await db.execute(select(Organization).where(Organization.is_demo_read_only.is_(True)))
        ).scalar_one_or_none()
        if org is None:
            logger.info("no demo org yet; skipping demo report seed")
            return

        await set_org_context(db, org.id)
        domain = (
            await db.execute(select(Domain).where(Domain.organization_id == org.id, Domain.name == DEMO_DOMAIN))
        ).scalar_one_or_none()
        if domain is None:
            logger.info("demo domain %s not found; skipping demo report seed", DEMO_DOMAIN)
            return

        # Pre-seed the source-IP -> service-label cache (no RLS on this table)
        # so the sender inventory shows friendly labels with no live PTR lookup.
        now = datetime.now(timezone.utc)
        identity_rows = []
        for ip, label, ptr, _hf, _out, _w, _v in SOURCES:
            if label:
                identity_rows.append({"source_ip": ip, "ptr_hostname": ptr, "service_label": label, "match_method": "pattern", "resolved_at": now})
            else:
                identity_rows.append({"source_ip": ip, "ptr_hostname": None, "service_label": ip, "match_method": "ip_fallback", "resolved_at": now})
        stmt = pg_insert(SourceIpIdentity).values(identity_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SourceIpIdentity.source_ip],
            set_={"ptr_hostname": stmt.excluded.ptr_hostname, "service_label": stmt.excluded.service_label, "match_method": stmt.excluded.match_method, "resolved_at": stmt.excluded.resolved_at},
        )
        await db.execute(stmt)

        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        inserted = 0
        for days_ago in range(WINDOW_DAYS, 0, -1):
            date = today - timedelta(days=days_ago)
            policy_p = _policy_for(days_ago)
            for reporter in REPORTERS:
                rng = random.Random(f"{reporter}:{date:%Y%m%d}")
                if rng.random() > 0.35:  # each reporter reports on ~1 in 3 days
                    continue
                report_id = f"demo-seed-{reporter.split()[0].lower()}-{date:%Y%m%d}"

                already = (
                    await db.execute(
                        select(DmarcAggregateReport.id).where(
                            DmarcAggregateReport.organization_id == org.id,
                            DmarcAggregateReport.org_name == reporter,
                            DmarcAggregateReport.report_id == report_id,
                            DmarcAggregateReport.policy_published_domain == DEMO_DOMAIN,
                        )
                    )
                ).scalar_one_or_none()
                if already is not None:
                    continue

                report = DmarcAggregateReport(
                    organization_id=org.id,
                    domain_id=domain.id,
                    report_id=report_id,
                    org_name=reporter,
                    email=None,
                    date_range_begin=date,
                    date_range_end=date + timedelta(days=1),
                    policy_published_domain=DEMO_DOMAIN,
                    policy_p=policy_p,
                    policy_sp=policy_p,
                    policy_pct=100,
                    policy_adkim="r",
                    policy_aspf="r",
                    source_message_id=f"demo-seed:{reporter}:{date:%Y%m%d}",
                    received_at=date + timedelta(days=1, hours=6),
                )
                db.add(report)
                await db.flush()

                records = []
                for ip, _label, _ptr, header_from, outcome, _w, vol in _weighted_subset(rng):
                    records.append(
                        DmarcAggregateRecord(
                            organization_id=org.id,
                            report_id=report.id,
                            domain_id=domain.id,
                            source_ip=ip,
                            count=rng.randint(*vol),
                            disposition=_disposition(outcome, policy_p),
                            dkim_result=AuthResult.pass_ if outcome in ("aligned", "dkim_only") else AuthResult.fail,
                            spf_result=AuthResult.pass_ if outcome == "aligned" else AuthResult.fail,
                            header_from=header_from,
                            envelope_from=header_from,
                            envelope_to=None,
                            auth_results=_auth_results(header_from, outcome),
                            policy_evaluated_reasons=None,
                            created_at=now,
                        )
                    )
                db.add_all(records)
                inserted += 1

        await db.commit()
        logger.info("demo report seed: %d new report(s) inserted for %s", inserted, DEMO_DOMAIN)


if __name__ == "__main__":
    asyncio.run(main())
