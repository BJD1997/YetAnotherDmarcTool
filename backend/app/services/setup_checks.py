"""Setup mistakes worth a "SETUP PROBLEM:" line in the api's log, so whoever
set the server up sees what to fix where they look first: startup checks
(settings, database, DNS), per-request checks (reverse proxy), and a
periodic check that a worker is running. Each says what's wrong and what to
change; setups that are fine, or deliberately split (Azure), stay quiet."""

import asyncio
import ipaddress
import logging
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.config import settings
from app.repositories.platform_admin import count_platform_admins
from app.services.auth.admin_access import admin_access_problem
from app.services.crypto.secrets import fernet_key_problem
from app.services.dns_checks.resolver import DnsLookupError, resolve_mx

logger = logging.getLogger("app.setup")

PREFIX = "SETUP PROBLEM: "
TIP_PREFIX = "SETUP TIP: "
_CONFIG_DOCS = "https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Configuration-Reference"
DEFAULT_PUBLIC_BASE_URL = "http://localhost:8000"
UPDATER_TOKEN_MIN_CHARS = 32
_ENV_HINT = "in the environment (the .env file, or the stack's environment variables in Portainer) and redeploy"
# Requests from inside the stack (health checks, the updater) aren't judged.
_UNJUDGED_PATHS = ("/api/health", "/api/update-request")


def _is_local_host(host: str) -> bool:
    """localhost, a loopback address, or a container name like "api"."""
    if host.startswith("["):
        name = host[1 : host.find("]")]
    elif host.count(":") == 1:
        name = host.split(":")[0]
    else:
        name = host
    name = name.lower()
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return name == "localhost" or "." not in name


def _together(*pairs: tuple[str, object]) -> bool:
    """All set, or all unset."""
    present = [bool(value) for _name, value in pairs]
    return all(present) or not any(present)


def database_password_problem(database_url: str) -> str | None:
    """The database user's password is still one of the compose files'
    examples: a stack started without its own still runs, so it gets a
    SETUP PROBLEM line instead of a refusal. The message is fixed text
    only: nothing from the URL gets logged."""
    password = make_url(database_url).password
    if password == "dmarc":
        variable, role = "POSTGRES_PASSWORD", "the database owner (POSTGRES_USER)"
    elif password == "dmarc_app":
        variable, role = "DMARC_APP_DB_PASSWORD", "dmarc_app"
    else:
        return None
    return (
        f"The database password for {role} is still the example default from the compose file. Change it in "
        f"the database first (ALTER ROLE ... PASSWORD '...'), then set {variable} to the same value {_ENV_HINT}."
    )


def config_problems() -> list[str]:
    found: list[str] = []
    fernet_problem = fernet_key_problem()
    if fernet_problem:
        found.append(fernet_problem)
    password_problem = database_password_problem(settings.database_url)
    if password_problem:
        found.append(password_problem)

    url = settings.public_base_url.rstrip("/")
    host = urlsplit(url).netloc
    if url == DEFAULT_PUBLIC_BASE_URL:
        found.append(
            f"PUBLIC_BASE_URL is still the default ({DEFAULT_PUBLIC_BASE_URL}). Set it to the address people use "
            "to reach this server, e.g. https://dmarc.example.com: sign-in cookies, setup links and Microsoft "
            f"sign-in depend on it. Set it {_ENV_HINT}."
        )
    elif url.startswith("http://") and not _is_local_host(host):
        found.append(
            f"PUBLIC_BASE_URL uses http:// ({url}), so sign-in cookies aren't marked secure and travel "
            "unencrypted. Put a reverse proxy with HTTPS in front and use its https:// address."
        )

    if not _together(("id", settings.entra_sso_client_id), ("secret", settings.entra_sso_client_secret)):
        found.append(
            "Microsoft sign-in is half set up: ENTRA_SSO_CLIENT_ID and ENTRA_SSO_CLIENT_SECRET go together. "
            f"Set both, or neither, {_ENV_HINT}."
        )
    # On Azure only the worker gets the mail app's secret (it does the syncs).
    if settings.deployment_platform != "azure-container-apps" and not _together(
        ("id", settings.entra_mail_client_id), ("secret", settings.entra_mail_client_secret)
    ):
        found.append(
            "The mailbox app is half set up: ENTRA_MAIL_CLIENT_ID and ENTRA_MAIL_CLIENT_SECRET go together. "
            f"Set both, or neither, {_ENV_HINT}."
        )
    if settings.hosted_reports_mailbox_address or settings.hosted_reports_tenant_id:
        missing = [
            name
            for name, value in (
                ("HOSTED_REPORTS_MAILBOX_ADDRESS", settings.hosted_reports_mailbox_address),
                ("HOSTED_REPORTS_TENANT_ID", settings.hosted_reports_tenant_id),
                ("ENTRA_MAIL_CLIENT_ID", settings.entra_mail_client_id),
            )
            if not value
        ]
        if missing:
            found.append(
                f"The hosted reporting mailbox is half set up: it also needs {', '.join(missing)}. "
                f"Set them {_ENV_HINT}, or remove the hosted mailbox settings."
            )
    if settings.cloudflare_api_token or settings.cloudflare_zone_id:
        if not (settings.cloudflare_api_token and settings.cloudflare_zone_id):
            found.append(
                "Cloudflare is half set up: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ZONE_ID go together. "
                f"Set both, or neither, {_ENV_HINT}."
            )
        if not settings.hosted_reports_mailbox_address:
            found.append(
                "Cloudflare settings are only used with the hosted reporting mailbox "
                "(HOSTED_REPORTS_MAILBOX_ADDRESS), which isn't set, so they do nothing."
            )
    if settings.deployment_platform == "azure-container-apps" and settings.effective_starttls_mode == "probe":
        found.append(
            "The STARTTLS check is set to probe port 25, which Azure blocks, so it fails for every domain. "
            "Set STARTTLS_CHECK_MODE=tls_rpt (results from TLS-RPT reports) or off on the api and worker, "
            "or redeploy with starttlsCheckMode=tls_rpt."
        )
    # The Docker Compose files always point the api at the updater; it only
    # accepts requests carrying this secret. (Portainer and Azure update
    # another way and don't set UPDATER_URL.)
    if settings.updater_url and not settings.updater_shared_secret and settings.deployment_platform == "portainer":
        pass  # optional on Portainer: a tip, see config_tips()
    elif settings.updater_url and not settings.updater_shared_secret:
        found.append(
            "UPDATER_SHARED_SECRET isn't set, so Update now in the admin console can't work: the updater "
            "rejects every request without it. Generate one with: openssl rand -hex 32 — and set it "
            f"{_ENV_HINT}."
        )
    elif settings.updater_url and len(settings.updater_shared_secret or "") < UPDATER_TOKEN_MIN_CHARS:
        # The limit is written out, not interpolated: a value derived from the
        # secret's settings shouldn't flow into a log line, even its length rule.
        found.append(
            "UPDATER_SHARED_SECRET is too short (under 32 characters). It lets the "
            "api replace the app's containers, so it should be hard to guess: generate one with: "
            f"openssl rand -hex 32 — and set it {_ENV_HINT}."
        )
    return found


def config_tips() -> list[str]:
    """Optional integrations that aren't set up, with what they'd add. Not
    problems: a setup without them works."""
    tips: list[str] = []
    if settings.deployment_platform == "portainer" and not settings.updater_shared_secret:
        tips.append(
            "Update now isn't set up, so updates mean changing IMAGE_TAG and redeploying the stack by hand. To "
            "update from the admin console instead, set UPDATER_SHARED_SECRET (openssl rand -hex 32), "
            "PORTAINER_URL, PORTAINER_API_KEY (a Portainer access token) and PORTAINER_STACK_ID (the id= in "
            "the stack's Portainer address). See https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Deploying-with-Portainer"
        )
    if not settings.entra_sso_client_id:
        tips.append(
            "Microsoft sign-in isn't set up, so organizations sign in with local accounts only. To let them "
            f"use their Microsoft 365 accounts, set ENTRA_SSO_CLIENT_ID and ENTRA_SSO_CLIENT_SECRET. See {_CONFIG_DOCS}"
        )
    if not settings.entra_mail_client_id:
        tips.append(
            "Reading DMARC reports from an organization's own Microsoft 365 mailbox isn't set up. To offer it, "
            f"set ENTRA_MAIL_CLIENT_ID and ENTRA_MAIL_CLIENT_SECRET. See {_CONFIG_DOCS}"
        )
    if not settings.hosted_reports_mailbox_address:
        tips.append(
            "There's no hosted reporting mailbox, so every organization needs a mailbox of its own for reports. "
            "To give each domain a ready-made reporting address instead, set HOSTED_REPORTS_MAILBOX_ADDRESS and "
            f"HOSTED_REPORTS_TENANT_ID (it uses the mailbox app above). See {_CONFIG_DOCS}"
        )
    elif not (settings.cloudflare_api_token or settings.cloudflare_zone_id):
        tips.append(
            "Hosted reporting addresses need a DMARC authorization record in the mailbox domain's DNS, added by "
            "hand for now. To have them created and removed automatically, set CLOUDFLARE_API_TOKEN and "
            f"CLOUDFLARE_ZONE_ID. See {_CONFIG_DOCS}"
        )
    return tips


async def database_problems(db: AsyncSession) -> list[str]:
    found: list[str] = []
    admin = await admin_access_problem(db)
    if admin:
        found.append(admin)
    elif settings.platform_admin_bootstrap_password and await count_platform_admins(db) > 0:
        found.append(
            "PLATFORM_ADMIN_BOOTSTRAP_PASSWORD is still set, but the first admin exists and it isn't used "
            "anymore. Remove it (and PLATFORM_ADMIN_BOOTSTRAP_EMAIL) from the environment so the password "
            "isn't lying around."
        )
    return found


async def dns_problem() -> str | None:
    try:
        await asyncio.wait_for(resolve_mx("gmail.com"), timeout=10)
    except (DnsLookupError, TimeoutError, OSError) as exc:
        return (
            f"DNS lookups through the resolver at DNS_RESOLVER_HOST={settings.dns_resolver_host} fail ({exc}). "
            "Every DNS check will fail until it works: check that the resolver container is running and "
            "reachable from the api and worker."
        )
    return None


def request_problem(path: str, host: str, client: str | None, headers: dict, scheme: str) -> str | None:
    """A problem a single request shows: a reverse proxy that isn't trusted,
    or no reverse proxy at all while PUBLIC_BASE_URL is https."""
    if path in _UNJUDGED_PATHS or _is_local_host(host or ""):
        return None
    forwarded_for = headers.get("x-forwarded-for")
    if forwarded_for:
        chain = [ip.strip() for ip in forwarded_for.split(",")]
        if client and client not in chain:
            return (
                f"Requests come through a reverse proxy at {client} that isn't trusted, so the sign-in log and "
                "rate limits see the proxy's address instead of each visitor's. Add it to FORWARDED_ALLOW_IPS "
                f"(e.g. FORWARDED_ALLOW_IPS={client}) {_ENV_HINT}."
            )
        return None
    if scheme == "http" and not headers.get("x-forwarded-proto") and settings.public_base_url.startswith("https://"):
        return (
            f"This server is being reached directly over http ({host}), not through an HTTPS reverse proxy. "
            "PUBLIC_BASE_URL is https, so browsers drop the sign-in cookie and nobody can sign in this way. "
            "Put a reverse proxy with HTTPS in front (e.g. Nginx Proxy Manager, Traefik, Caddy or a Cloudflare "
            "Tunnel) and open the PUBLIC_BASE_URL address."
        )
    return None


_warned: set[str] = set()


def reset_request_warnings() -> None:
    _warned.clear()


def note_request(path: str, host: str, client: str | None, headers: dict, scheme: str) -> None:
    """Logs a request's problem once per process, not on every request."""
    problem = request_problem(path, host, client, headers, scheme)
    if problem:
        tag = " ".join(problem.split()[:4])  # one warning per kind, not per address
        if tag not in _warned:
            _warned.add(tag)
            logger.warning(PREFIX + problem)


async def worker_running(conn: AsyncConnection | AsyncSession) -> bool:
    """Whether a worker holds the scheduler lock (see app/services/jobs/leader.py)."""
    key = settings.leader_lock_key
    result = await conn.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted AND objsubid = 1 "
            "AND classid::bigint = :hi AND objid::bigint = :lo)"
        ),
        {"hi": key >> 32, "lo": key & 0xFFFFFFFF},
    )
    return bool(result.scalar())


NO_WORKER = (
    "No worker is running (nobody holds the scheduler lock), so mailbox syncs, DNS checks, notifications and "
    "updates don't happen. Check that the worker container is running, and look at its log."
)
