"""Orchestrates running every Phase 3 checker for one domain and normalizes
the results into a dict keyed by CheckType, ready for the caller (see
app/routers/dns_checks.py) to persist as dns_check_results rows."""

from app.models.enums import CheckType, DomainMailProfile, SpfAllQualifierMode
from app.services.dns_checks import dane, dkim, dmarc, dmarcbis, mta_sts, mx, spf, starttls, tls_rpt_check
from app.services.dns_checks.base import Finding
from app.services.dns_checks.dmarc_record import resolve_effective_policy


async def run_all(
    domain_name: str,
    dkim_selectors: list[str],
    mail_profile: DomainMailProfile = DomainMailProfile.sends_mail,
    spf_all_qualifier_mode: SpfAllQualifierMode = SpfAllQualifierMode.strict,
    parent_domain_name: str | None = None,
    mailbox_address: str | None = None,
) -> dict[CheckType, list[Finding]]:
    # Fetched once here (rather than inside spf.py itself) since spf.py is a
    # pure structural linter with no DNS calls of its own beyond the SPF
    # chain — this is the one extra lookup needed for conditional mode to
    # know whether DMARC is actually enforcing yet. Falls back to the
    # parent's policy when this domain has no DMARC record of its own (the
    # normal case for a subdomain, RFC 7489 inheritance) — otherwise a
    # subdomain never gets credited for its own ~all under conditional
    # mode, since dmarc_policy would silently resolve to None even though
    # its mail really is being enforced under the parent's p=reject.
    dmarc_policy = await resolve_effective_policy(domain_name, parent_domain_name)

    return {
        CheckType.spf: await spf.check(domain_name, mail_profile, dmarc_policy, spf_all_qualifier_mode),
        CheckType.dkim: await dkim.check(domain_name, dkim_selectors, mail_profile),
        CheckType.dmarc: await dmarc.check(domain_name, parent_domain_name, mailbox_address),
        CheckType.dmarcbis: await dmarcbis.check(domain_name),
        CheckType.mx: await mx.check(domain_name),
        CheckType.mta_sts: await mta_sts.check(domain_name),
        CheckType.dane: await dane.check(domain_name),
        CheckType.tls_rpt: await tls_rpt_check.check(domain_name, mailbox_address),
        CheckType.starttls: await starttls.check(domain_name),
    }
