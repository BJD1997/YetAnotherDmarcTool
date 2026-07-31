from app.services.dns_checks.resolver import resolve_txt

VERIFICATION_LABEL = "_dmarc-dashboard-verify"


def verification_record_name(domain_name: str) -> str:
    return f"{VERIFICATION_LABEL}.{domain_name}"


async def verify_domain_ownership(domain_name: str, token: str) -> bool:
    values = await resolve_txt(verification_record_name(domain_name))
    return any(token in value for value in values)
