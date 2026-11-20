"""Mail services and their SPF include values: names an include (or MX host)
for the SPF check, Ask AI and the SPF record builder, and lists the
services the builder offers to add.

SPF_PROVIDERS holds each service's documented include value, exactly as
its own setup guide gives it. KNOWN_MAIL_SERVICES is looser: substrings
that recognize a service in MX hosts and in include values the catalog
doesn't list verbatim (regional or older variants)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpfProvider:
    key: str
    label: str
    include: str
    # Sender names from source identification (source_identification/
    # patterns.py) that are this service, so an approved sender can be
    # matched to the include it needs. Empty when the name doesn't say which
    # include applies (Zoho's differs per region).
    sender_labels: tuple[str, ...] = ()


SPF_PROVIDERS: list[SpfProvider] = [
    SpfProvider("microsoft365", "Microsoft 365", "spf.protection.outlook.com", ("Microsoft 365",)),
    SpfProvider("google", "Google Workspace", "_spf.google.com", ("Google Workspace",)),
    SpfProvider("icloud", "iCloud Mail", "icloud.com"),
    SpfProvider("proton", "Proton Mail", "_spf.protonmail.ch"),
    SpfProvider("zoho_eu", "Zoho Mail (EU)", "zoho.eu"),
    SpfProvider("zoho", "Zoho Mail (US)", "zoho.com"),
    SpfProvider("smtp2go", "SMTP2GO", "spf.smtp2go.com", ("SMTP2GO",)),
    SpfProvider("sendgrid", "SendGrid", "sendgrid.net", ("SendGrid",)),
    SpfProvider("mailgun", "Mailgun", "mailgun.org", ("Mailgun",)),
    SpfProvider("amazonses", "Amazon SES", "amazonses.com", ("Amazon SES",)),
    SpfProvider("mandrill", "Mailchimp Transactional", "spf.mandrillapp.com", ("Mandrill",)),
    SpfProvider("postmark", "Postmark", "spf.mtasv.net"),
    SpfProvider("mailjet", "Mailjet", "spf.mailjet.com"),
]

KNOWN_MAIL_SERVICES: list[tuple[tuple[str, ...], str]] = [
    (("protection.outlook.com", "outlook.com", "mx.microsoft"), "Microsoft 365"),
    (("google.com", "googlemail.com"), "Google Workspace"),
    (("icloud.com",), "iCloud Mail"),
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
    (("postmarkapp", "mtasv.net"), "Postmark"),
    (("mailjet",), "Mailjet"),
    (("sparkpostmail",), "SparkPost"),
    (("messagelabs",), "Broadcom Email Security"),
]


def service_for_host(host: str) -> str | None:
    """The mail service an MX host or include value belongs to, if known."""
    host = host.rstrip(".").lower()
    for provider in SPF_PROVIDERS:
        if host == provider.include:
            return provider.label
    for patterns, name in KNOWN_MAIL_SERVICES:
        if any(p in host for p in patterns):
            return name
    return None


def provider_for_sender(sender_label: str) -> SpfProvider | None:
    return next((p for p in SPF_PROVIDERS if sender_label in p.sender_labels), None)
