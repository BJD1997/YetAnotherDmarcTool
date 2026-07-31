"""Hostname-suffix -> friendly sending-service name. Deliberately a small,
hand-maintained list rather than a paid IP-intelligence API (self-hosted
tool, no outbound dependency beyond DNS) — extend as real data surfaces
services not yet covered here."""

KNOWN_SERVICE_PATTERNS: list[tuple[str, str]] = [
    (".protection.outlook.com", "Microsoft 365"),
    (".mx.microsoft", "Microsoft 365"),
    (".smtp2go.com", "SMTP2GO"),
    (".smtp2go.net", "SMTP2GO"),
    (".sendgrid.net", "SendGrid"),
    (".amazonses.com", "Amazon SES"),
    (".google.com", "Google Workspace"),
    (".googlemail.com", "Google Workspace"),
    (".mimecast.com", "Mimecast"),
    (".pphosted.com", "Proofpoint"),
    (".mailgun.org", "Mailgun"),
    (".mandrillapp.com", "Mandrill"),
    (".zoho.com", "Zoho Mail"),
]


def match_known_service(hostname: str) -> str | None:
    hostname = hostname.rstrip(".").lower()
    for suffix, label in KNOWN_SERVICE_PATTERNS:
        if hostname.endswith(suffix):
            return label
    return None
