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
