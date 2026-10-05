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
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx, resolve_txt
from app.services.dns_checks.tls_rpt_check import fetch_current_tls_rpt_record
from app.services.rating.domain_rating import latest_findings_by_type, rating_window_days

KINDS = ("dns_check", "sender", "compliance")
MAX_PROMPT_CHARS = 6000
MAX_IPS = 10

INTRO = "I monitor DMARC for my domain and need help fixing an issue."
OUTRO = """Please answer with:
1. The likely cause.
2. Step-by-step fixes, with the exact DNS records or settings, and where to change them for the mail services above.
3. How to check it worked (for example, what the next DMARC reports should show).
4. Risks: anything that could stop legitimate mail, such as tightening the DMARC policy before every sender passes.
Ask me if you need more information. The data comes from my DMARC aggregate reports and DNS."""
MAX_ISSUE_CHARS = 300

# Mail services recognized from MX hosts and SPF includes, so the answer can
# give steps for the right admin portal.
_PROVIDERS: list[tuple[tuple[str, ...], str]] = [
    (("protection.outlook.com", "outlook.com", "mx.microsoft"), "Microsoft 365"),
    (("google.com", "googlemail.com"), "Google Workspace"),
    (("smtp2go",), "SMTP2GO"),
    (("sendgrid",), "SendGrid"),
    (("mailgun",), "Mailgun"),
    (("amazonses",), "Amazon SES"),
    (("mcsv.net", "mandrillapp", "mailchimp"), "Mailchimp"),
    (("zoho",), "Zoho Mail"),
    (("protonmail", "proton.me"), "Proton Mail"),
    (("mimecast",), "Mimecast"),
    (("pphosted",), "Proofpoint"),
    (("sendinblue", "brevo"), "Brevo"),
    (("postmarkapp",), "Postmark"),
    (("sparkpostmail",), "SparkPost"),
    (("messagelabs",), "Broadcom Email Security"),
]

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


async def mx_hosts(domain_name: str) -> list[str]:
    try:
        return [host.rstrip(".").lower() for _pref, host in sorted(await resolve_mx(domain_name))]
    except DnsLookupError:
        return []


def _providers(hosts: list[str]) -> list[str]:
    found: list[str] = []
    for host in hosts:
        for patterns, name in _PROVIDERS:
            if any(p in host for p in patterns) and name not in found:
                found.append(name)
    return found


async def _mail_setup(domain_name: str) -> str | None:
    """e.g. "receives mail via Microsoft 365; SPF allows Microsoft 365, SMTP2GO"."""
    parts = []
    mx = await mx_hosts(domain_name)
    inbound = _providers(mx)
    if inbound:
        parts.append(f"receives mail via {', '.join(inbound)}")
    elif mx:
        parts.append(f"MX: {', '.join(mx[:3])}")
    spf = await current_record("spf", domain_name) or ""
    includes = [
        t.split(":", 1)[1] if t.lower().startswith("include:") else t.split("=", 1)[1]
        for t in spf.split()
        if t.lower().startswith(("include:", "redirect="))
    ]
    allowed = _providers([i.lower() for i in includes])
    if allowed:
        parts.append(f"SPF allows {', '.join(allowed)}")
    return f"Mail setup: {'; '.join(parts)}." if parts else None


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


def _verdict(status: str | None, likely_spoofed: bool) -> tuple[str, str]:
    """(short label, what to ask of the AI) for a sender, from its review in
    the Senders list and the app's own likely-spoofed signal — so the answer
    doesn't try to "fix" mail that isn't the domain owner's."""
    if status == "approved":
        return "approved", "My verdict: approved, a legitimate sender of mine. Help me make it pass DMARC."
    if status == "blocked":
        return (
            "blocked: not mine",
            "My verdict: blocked, this is not my mail (spoofing or abuse). Don't suggest making it pass SPF, DKIM "
            "or DMARC. Tell me whether my DMARC policy already stops it, and whether anything else is worth doing.",
        )
    if status == "ignored":
        return (
            "ignored",
            "My verdict: ignored, I marked it as not relevant. Don't suggest making it pass; tell me if that "
            "verdict looks wrong from the data.",
        )
    if status == "archived":
        return (
            "no longer in use",
            "My verdict: no longer in use, I retired this sender. Don't suggest making it pass; help me find out "
            "why it still sends mail as my domain.",
        )
    if likely_spoofed:
        return (
            "not reviewed; looks spoofed",
            "My verdict: not reviewed yet, and it looks spoofed: receivers rejected or quarantined most of its mail "
            "and it passes neither SPF nor DKIM. First help me judge whether it's mine (sending IPs, reverse DNS). "
            "If it isn't, don't suggest making it pass: my DMARC policy is doing its job.",
        )
    return (
        "not reviewed",
        "My verdict: not reviewed yet. First help me judge whether it's a legitimate sender of mine; only if it "
        "is, how to make it pass DMARC.",
    )


def _pct(value) -> str:
    return "—" if value is None else f"{value}%"


async def _sender(db: AsyncSession, domain: Domain, subject: str | None) -> list[str]:
    since, days = await _since(db, domain)
    sender = next((s for s in await service_breakdown(db, domain.id, since=since) if s["service_label"] == subject), None)
    if sender is None:
        raise ValueError(f"no sender {subject!r} for this domain in the last {days} days")
    ips = sender["source_ips"][:MAX_IPS]
    details = await dmarc_reports_repo.sender_auth_details(
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
    for label, seen in (
        ("SPF (envelope-from) domains it uses", details["spf_domains"]),
        ("DKIM signing domains it uses", details["dkim_domains"]),
    ):
        if seen:
            top = sorted(seen.items(), key=lambda kv: -kv[1])[:5]
            lines.append(f"{label}: " + ", ".join(f"{name} ({count:,})" for name, count in top))
    if not details["dkim_selectors"]:
        lines.append("DKIM signatures seen: none (this sender's mail isn't DKIM-signed).")
    else:
        lines.append("DKIM signatures seen (selector, signing domain):")
        top = sorted(details["dkim_selectors"].items(), key=lambda kv: -(kv[1]["pass"] + kv[1]["fail"]))[:5]
        for (dkim_domain, selector), tally in top:
            lines.append(f"- {selector} ({dkim_domain}): {tally['pass']:,} passed, {tally['fail']:,} failed")
    if details["reporters"]:
        top = sorted(details["reporters"].items(), key=lambda kv: -kv[1])[:5]
        lines.append("Reported by: " + ", ".join(f"{name} ({count:,})" for name, count in top))
    lines.append(_record_line("SPF", await current_record("spf", domain.name)))
    lines.append(_record_line("DMARC", await current_record("dmarc", domain.name)))
    lines.append("For DMARC to pass, SPF or DKIM must pass with a domain that aligns with the From domain.")
    # Right under the sender line: the verdict decides what kind of help fits.
    review = await dmarc_reports_repo.get_sender_review(db, domain.id, sender["service_label"])
    lines.insert(2, _verdict(review.status.value if review else None, sender.get("likely_spoofed", False))[1])
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
        reviews = {r.service_label: r.status.value for r in await dmarc_reports_repo.list_sender_reviews_for_domain(db, domain.id)}
        lines.append("Senders failing the most, with my verdict on each:")
        for s in failing:
            label, _ask = _verdict(reviews.get(s["service_label"]), s.get("likely_spoofed", False))
            lines.append(
                f'- "{s["service_label"]}" ({label}): {s["volume"]:,} messages, {_pct(s["dmarc_pass_pct"])} pass DMARC, '
                f'{_pct(s["spf_aligned_pct"])} SPF aligned, {_pct(s["dkim_aligned_pct"])} DKIM aligned'
            )
        lines.append(
            "Senders I blocked or that look spoofed should keep failing: only suggest fixes for legitimate senders."
        )
    lines.append(_record_line("DMARC", await current_record("dmarc", domain.name)))
    return lines


_BUILDERS = {"dns_check": _dns_check, "sender": _sender, "compliance": _compliance}


async def build_prompt(
    db: AsyncSession, domain: Domain, kind: str, subject: str | None, issue: str | None = None
) -> str:
    """`issue` is the issue as the user saw it (an Action queue item's title
    and hint, a check's summary), so the question says what's being asked
    about, not just the data."""
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"unknown kind {kind!r}")
    head = [INTRO]
    if issue and issue.strip():
        head.append(f"The issue: {' '.join(issue.split())[:MAX_ISSUE_CHARS]}")
    body = await builder(db, domain, subject)
    setup = await _mail_setup(domain.name)
    if setup:
        body.insert(1, setup)
    return cap(redact("\n".join(head) + "\n\n" + "\n".join(body) + "\n\n" + OUTRO))
