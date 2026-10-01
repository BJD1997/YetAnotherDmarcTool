"""Create the non-owner `dmarc_app` runtime role idempotently, using the admin
DATABASE_URL. This is the managed-Postgres equivalent of
db/init/01-create-app-role.sh, which only runs on the docker-compose Postgres
(via docker-entrypoint-initdb.d) — a managed server (e.g. Azure Database for
PostgreSQL Flexible Server) has no such hook.

Run FIRST in the deploy migrate step — before `alembic upgrade head` (whose
GRANTs target this role) and before api/worker connect as it. Reads the role's
password from DMARC_APP_DB_PASSWORD, and the admin connection from
settings.database_url (the migrate job's DATABASE_URL is the Postgres admin
login, not the dmarc_app connection api/worker use).

Runs on every migrate, so it must work for an admin that isn't a superuser:
Azure's admin may not even name the SUPERUSER attribute in ALTER ROLE, so an
existing role only gets the attributes that differ. The password is sent as a
SCRAM-SHA-256 verifier, never in plain text, and errors don't echo the SQL.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ensure_app_role")

_ROLE = "dmarc_app"
_SCRAM_ITERATIONS = 4096  # Postgres' default scram_iterations


def scram_verifier(password: str, salt: bytes | None = None) -> str:
    """The SCRAM-SHA-256 verifier Postgres stores for `password` (RFC 5802),
    so the plain password never appears in SQL, statement logs or errors."""
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${_SCRAM_ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


async def ensure_role(db: AsyncSession, role: str, password: str, db_name: str | None) -> None:
    verifier = scram_verifier(password)  # base64 and $:, so no quoting needed
    # Raw, not text(): text() would read the verifier's ":..." as bind parameters.
    raw = (await db.connection()).exec_driver_sql
    row = (
        await db.execute(
            text("SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = :r"),
            {"r": role},
        )
    ).first()
    if row is None:
        await raw(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{verifier}'")
        logger.info("created role %s", role)
    else:
        attrs = [
            attr
            for attr, needed in (
                ("LOGIN", not row.rolcanlogin),
                ("NOSUPERUSER", row.rolsuper),
                ("NOCREATEDB", row.rolcreatedb),
                ("NOCREATEROLE", row.rolcreaterole),
            )
            if needed
        ]
        await raw(f"ALTER ROLE {role} WITH {' '.join(attrs)} PASSWORD '{verifier}'")
        logger.info("role %s already exists — password refreshed%s", role, f", set {' '.join(attrs)}" if attrs else "")
    if db_name:
        await db.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {role}'))
    await db.commit()


async def main() -> None:
    password = os.environ.get("DMARC_APP_DB_PASSWORD")
    if not password:
        raise SystemExit("DMARC_APP_DB_PASSWORD is not set — cannot create the dmarc_app role")
    db_name = make_url(settings.database_url).database
    async with async_session_factory() as db:
        try:
            await ensure_role(db, _ROLE, password, db_name)
        except Exception as exc:
            # Not the SQLAlchemy error itself: its message carries the statement.
            orig = getattr(exc, "orig", None)
            raise SystemExit(f"setting up role {_ROLE} failed: {type(orig or exc).__name__}: {orig or exc}") from None


if __name__ == "__main__":
    asyncio.run(main())
