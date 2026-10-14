"""STARTTLS result from TLS-RPT reports (STARTTLS_CHECK_MODE=tls_rpt): what
real senders (Google, Microsoft, ...) report about TLS to this domain's MX
hosts, for deployments that can't probe port 25 themselves (Azure blocks
it). Summary counts are per policy domain; failure details name the MX host
and the failure type."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dmarc_reports import tls_rpt_session_totals_for_domain
from app.services.dns_checks.base import Finding
from app.services.dns_checks.tls_rpt_check import check_tls_rpt_rua_destination

WINDOW_DAYS = 14
MAX_FAILURE_RATE = 0.02


def _failure_summary(failure_details: list[dict]) -> str:
    by_host: dict[str, dict[str, int]] = {}
    for item in failure_details:
        host = item.get("receiving_mx_hostname") or "unknown host"
        kind = item.get("result_type") or "unknown"
        kinds = by_host.setdefault(host, {})
        kinds[kind] = kinds.get(kind, 0) + int(item.get("failed_session_count") or 0)
    return "; ".join(
        f"{host}: " + ", ".join(f"{kind} ({count})" for kind, count in sorted(kinds.items(), key=lambda kv: -kv[1]))
        for host, kinds in sorted(by_host.items())
    )


def evaluate(rua_status: str, success: int, failure: int, failure_details: list[dict]) -> list[Finding]:
    """No finding when the domain's TLS-RPT doesn't reach this app (the
    TLS-RPT check already says why); pending until reports arrive; then
    pass, warn (over MAX_FAILURE_RATE failed) or fail (nothing succeeded)."""
    if rua_status != "correct":
        return []
    total = success + failure
    if total == 0:
        return [Finding(status="pending", summary=f"Waiting for TLS-RPT reports (none in the last {WINDOW_DAYS} days)")]
    details = {"successful_sessions": success, "failed_sessions": failure, "window_days": WINDOW_DAYS, "source": "tls_rpt"}
    if success == 0:
        return [
            Finding(
                status="fail",
                summary=f"No TLS session succeeded in {failure:,} reported: {_failure_summary(failure_details)}",
                details=details,
            )
        ]
    if failure / total > MAX_FAILURE_RATE:
        return [
            Finding(
                status="warn",
                summary=f"{failure:,} of {total:,} reported TLS sessions failed: {_failure_summary(failure_details)}",
                details=details,
            )
        ]
    return [
        Finding(
            status="pass",
            summary=f"{success:,} of {total:,} reported TLS sessions succeeded in the last {WINDOW_DAYS} days",
            details=details,
        )
    ]


async def check(db: AsyncSession, domain_id: uuid.UUID, domain_name: str, mailbox_address: str | None) -> list[Finding]:
    if not mailbox_address:
        return []
    rua_status = (await check_tls_rpt_rua_destination(domain_name, mailbox_address)).status
    if rua_status != "correct":
        return []
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    success, failure, failure_details = await tls_rpt_session_totals_for_domain(db, domain_id, since)
    return evaluate(rua_status, success, failure, failure_details)
