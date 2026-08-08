from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"

    # Public HTTPS URL this app is reachable at through NPM, e.g. https://dmarc.example.com
    public_base_url: str = "http://localhost:8000"

    database_url: str = "postgresql+asyncpg://dmarc:dmarc@db:5432/dmarc"

    # Sessions (Phase 1)
    session_cookie_name: str = "dmarc_session"
    platform_admin_session_cookie_name: str = "dmarc_admin_session"
    session_idle_timeout_hours: int = 12
    session_absolute_timeout_days: int = 7

    # Local email+password+TOTP login (for orgs with no entra_tenant_id set)
    mfa_pending_cookie_name: str = "dmarc_mfa_pending"
    mfa_pending_timeout_minutes: int = 10
    password_setup_token_timeout_hours: int = 72

    # The operator's own shared reporting mailbox — <mailbox-local-part>+<tag>@<domain>
    # addresses issued via POST /domains/{id}/hosted-report-address all land
    # here via plus-addressing, polled by app/workers/jobs/hosted_reports_poll_job.py.
    # Unset until the domain/mailbox actually exist; the job simply no-ops
    # until both are configured (see app/workers/scheduler.py).
    hosted_reports_tenant_id: str | None = None
    hosted_reports_mailbox_address: str | None = None
    # The domain new addresses are generated under — MUST be the same domain
    # as hosted_reports_mailbox_address (e.g. both "davidshosting.nl"), not
    # just a separately-configurable value: plus-addressing only resolves
    # <local>+<tag>@domain to <local>@domain on that exact domain, there's no
    # cross-domain routing. A domain-wide catch-all would relax this, but
    # isn't available on every provider (e.g. Exchange Online has none) —
    # see the comment in app/routers/domains.py's hosted-report-address
    # generation for the full reasoning.
    hosted_reports_address_domain: str | None = None

    # Cloudflare API credentials for the zone hosting hosted_reports_address_domain
    # — used to auto-create the RFC 7489 §7.1 authorization record
    # (<client-domain>._report._dmarc.<hosted_reports_address_domain>) each
    # client domain needs before receivers will honor rua= pointing at a
    # hosted address. See app/services/cloudflare/dns_provisioner.py.
    cloudflare_api_token: str | None = None
    cloudflare_zone_id: str | None = None

    # Fernet key for the optional per-org custom-app-credential escape hatch (Phase 1+)
    fernet_key: str | None = None

    # Entra App A — Mail Access (application permission, Graph client-credentials) (Phase 2)
    entra_mail_client_id: str | None = None
    entra_mail_client_secret: str | None = None

    # Entra App B — Dashboard SSO (delegated, OIDC) (Phase 1)
    entra_sso_client_id: str | None = None
    entra_sso_client_secret: str | None = None
    # "organizations" excludes personal Microsoft accounts — this is a B2B tool.
    entra_sso_authority: str = "https://login.microsoftonline.com/organizations"

    # Bootstraps the first platform_admins row if the table is empty
    # (see app/scripts/bootstrap_platform_admin.py, run by the `migrate` container).
    platform_admin_bootstrap_email: str | None = None
    platform_admin_bootstrap_password: str | None = None

    # DNS resolver to send checker queries through (the `resolver` container) (Phase 3)
    dns_resolver_host: str = "resolver"

    @property
    def entra_sso_redirect_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/callback"


settings = Settings()
