"""Orchestrates running every Phase 3 checker for one domain and normalizes
the results into a dict keyed by CheckType, ready for the caller (see
app/routers/dns_checks.py) to persist as dns_check_results rows."""

from app.models.enums import CheckType
from app.services.dns_checks import dane, dkim, dmarc, dmarcbis, mta_sts, mx, spf, tls_rpt_check
from app.services.dns_checks.base import Finding


async def run_all(domain_name: str, dkim_selectors: list[str]) -> dict[CheckType, list[Finding]]:
    return {
        CheckType.spf: await spf.check(domain_name),
        CheckType.dkim: await dkim.check(domain_name, dkim_selectors),
        CheckType.dmarc: await dmarc.check(domain_name),
        CheckType.dmarcbis: await dmarcbis.check(domain_name),
        CheckType.mx: await mx.check(domain_name),
        CheckType.mta_sts: await mta_sts.check(domain_name),
        CheckType.dane: await dane.check(domain_name),
        CheckType.tls_rpt: await tls_rpt_check.check(domain_name),
    }
