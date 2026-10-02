"""Ask AI: turns one issue into a question for an AI assistant, which the
user previews and opens in their own AI account (the app never contacts an
AI service). Built here, not in the browser, so what may be shared is
decided in one tested place:

- in: domain names, public DNS records, check findings, sender names, IPs
  and reverse DNS, pass rates and message counts;
- never: the organization's name, users' email addresses, mailbox or
  reporting addresses (redact() replaces every email address, which also
  covers rua=/ruf= values and check summaries quoting them), raw reports.
"""

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.enums import CheckType
from app.repositories import dmarc_reports as dmarc_reports_repo
from app.repositories.trends import get_trend
from app.services.dmarc_analytics import service_breakdown
from app.services.dns_checks.dmarc_record import fetch_current_dmarc_record
from app.services.dns_checks.resolver import DnsLookupError, resolve_txt
from app.services.dns_checks.tls_rpt_check import fetch_current_tls_rpt_record
from app.services.rating.domain_rating import latest_findings_by_type, rating_window_days

KINDS = ("dns_check", "sender", "compliance")
MAX_PROMPT_CHARS = 6000
MAX_IPS = 10

INTRO = (
    "I run DMARC monitoring for my domain. Help me fix the issue below. Explain the likely cause, "
    "then give step-by-step fixes with exact DNS records where relevant. Ask if you need more information."
)
OUTRO = "Data from my DMARC aggregate reports and DNS."

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CHECK_NAMES = {
    CheckType.spf: "SPF",
    CheckType.dkim: "DKIM",
    CheckType.dmarc: "DMARC",
    CheckType.dmarcbis: "DMARCbis readiness",
    CheckType.mx: "MX",
    CheckType.starttls: "STARTTLS",
    CheckType.mta_sts: "MTA-STS",
    CheckType.dane: "DANE",
    CheckType.tls_rpt: "TLS-RPT",
}


def redact(text: str) -> str:
    return _EMAIL.sub("<your reporting address>", text)


def cap(text: str) -> str:
    if len(text) <= MAX_PROMPT_CHARS:
        return text
    note = "\n\n(Shortened to fit; ask me for the rest if you need it.)"
    cut = text[: MAX_PROMPT_CHARS - len(note)]
    if "\n\n" in cut:
        cut = cut[: cut.rindex("\n\n")]
    return cut + note


async def current_record(kind: str, domain_name: str) -> str | None:
    """The record as published right now, or None (absent or lookup failed)."""
    try:
        if kind == "spf":
            return next((r for r in await resolve_txt(domain_name) if r.lower().startswith("v=spf1")), None)
        if kind == "dmarc":
            record = await fetch_current_dmarc_record(domain_name)
            return record.raw if record else None
        if kind == "tls_rpt":
            record = await fetch_current_tls_rpt_record(domain_name)
            return record.raw if record else None
        if kind == "mta_sts":
            return next((r for r in await resolve_txt(f"_mta-sts.{domain_name}") if r.startswith("v=STSv1")), None)
    except DnsLookupError:
        return None
    return None


def _record_line(label: str, record: str | None) -> str:
    return f"Current {label} record: {record}" if record else f"Current {label} record: none published"


async def _dns_check(db: AsyncSession, domain: Domain, subject: str | None) -> list[str]:
    try:
        check_type = CheckType(subject)
    except ValueError as exc:
        raise ValueError(f"unknown check {subject!r}") from exc
    findings = (await latest_findings_by_type(db, domain.id)).get(check_type) or []
    name = _CHECK_NAMES.get(check_type, check_type.value)
    lines = [f"Domain: {domain.name} ({domain.mail_profile.value.replace('_', ' ')})", f"Check: {name}"]
    if findings:
        lines.append("Findings:")
        for f in findings:
            subject_part = f" [{f.subject}]" if f.subject else ""
            lines.append(f"- {f.status.value}{subject_part}: {f.summary}")
            recommendation = (f.details or {}).get("recommendation")
            if recommendation:
                lines.append(f"  Suggested: {recommendation}")
    if check_type.value in ("spf", "dmarc", "tls_rpt", "mta_sts"):
        lines.append(_record_line(name, await current_record(check_type.value, domain.name)))
    return lines


async def _since(db: AsyncSession, domain: Domain) -> tuple[datetime, int]:
    days = await rating_window_days(db, domain.organization_id)
    return datetime.now(timezone.utc) - timedelta(days=days), days


def _pct(value) -> str:
    return "—" if value is None else f"{value}%"


async def _sender(db: AsyncSession, domain: Domain, subject: str | None) -> list[str]:
    since, days = await _since(db, domain)
    sender = next((s for s in await service_breakdown(db, domain.id, since=since) if s["service_label"] == subject), None)
    if sender is None:
        raise ValueError(f"no sender {subject!r} for this domain in the last {days} days")
    ips = sender["source_ips"][:MAX_IPS]
    spf_domains, dkim_domains = await dmarc_reports_repo.auth_domains_for_ips(
        db, domain.id, [ip["source_ip"] for ip in sender["source_ips"]], since
    )
    lines = [
        f"Domain: {domain.name}",
        f'Sender: "{sender["service_label"]}", last {days} days: {sender["volume"]:,} messages, '
        f'{_pct(sender["dmarc_pass_pct"])} pass DMARC, {_pct(sender["spf_aligned_pct"])} SPF aligned, '
        f'{_pct(sender["dkim_aligned_pct"])} DKIM aligned; {sender["accepted"]:,} delivered, '
        f'{sender["quarantined"]:,} quarantined, {sender["rejected"]:,} rejected by receivers.',
        "Sending IPs:",
    ]
    for ip in ips:
        ptr = ip["ptr_hostname"] or "no reverse DNS"
        lines.append(f'- {ip["source_ip"]} ({ptr}): {ip["volume"]:,} messages')
    if len(sender["source_ips"]) > MAX_IPS:
        lines.append(f'- and {len(sender["source_ips"]) - MAX_IPS} more')
    for label, seen in (("SPF (envelope-from) domains it uses", spf_domains), ("DKIM signing domains it uses", dkim_domains)):
        if seen:
            top = sorted(seen.items(), key=lambda kv: -kv[1])[:5]
            lines.append(f"{label}: " + ", ".join(f"{name} ({count:,})" for name, count in top))
    lines.append(_record_line("SPF", await current_record("spf", domain.name)))
    lines.append(_record_line("DMARC", await current_record("dmarc", domain.name)))
    lines.append("For DMARC to pass, SPF or DKIM must pass with a domain that aligns with the From domain.")
    return lines


async def _compliance(db: AsyncSession, domain: Domain, _subject: str | None) -> list[str]:
    since, days = await _since(db, domain)
    total, passed = await dmarc_reports_repo.windowed_totals_excluding_blocked(db, domain.id, since)
    policy = await dmarc_reports_repo.latest_published_policy_for_domain(db, domain.id)
    pass_pct = f"{round(passed / total * 100, 1)}%" if total else "—"
    lines = [
        f"Domain: {domain.name} ({domain.mail_profile.value.replace('_', ' ')})",
        f"Last {days} days: {total:,} messages, {pass_pct} pass DMARC. Published policy: p={policy or 'none published'}.",
    ]
    trend = await get_trend(db, domain.id)
    if trend and trend.state in ("up", "down") and trend.recent_pass_pct is not None:
        direction = "down" if trend.state == "down" else "up"
        lines.append(
            f"Trend: {direction}, {trend.recent_pass_pct}% this week vs {trend.baseline_pass_pct}% the 4 weeks before."
        )
    services = await service_breakdown(db, domain.id, since=since)
    failing = sorted(
        (s for s in services if s["dmarc_pass_pct"] is not None and s["dmarc_pass_pct"] < 100),
        key=lambda s: -s["volume"] * (1 - s["dmarc_pass_pct"] / 100),
    )[:3]
    if failing:
        lines.append("Senders failing the most:")
        for s in failing:
            lines.append(
                f'- "{s["service_label"]}": {s["volume"]:,} messages, {_pct(s["dmarc_pass_pct"])} pass DMARC, '
                f'{_pct(s["spf_aligned_pct"])} SPF aligned, {_pct(s["dkim_aligned_pct"])} DKIM aligned'
            )
    lines.append(_record_line("DMARC", await current_record("dmarc", domain.name)))
    return lines


_BUILDERS = {"dns_check": _dns_check, "sender": _sender, "compliance": _compliance}


async def build_prompt(db: AsyncSession, domain: Domain, kind: str, subject: str | None) -> str:
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"unknown kind {kind!r}")
    body = "\n".join(await builder(db, domain, subject))
    return cap(redact(f"{INTRO}\n\n{body}\n\n{OUTRO}"))
