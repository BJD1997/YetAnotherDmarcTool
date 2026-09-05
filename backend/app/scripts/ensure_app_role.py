"""Create the non-owner `dmarc_app` runtime role idempotently, using the admin
DATABASE_URL. This is the managed-Postgres equivalent of
db/init/01-create-app-role.sh, which only runs on the docker-compose Postgres
(via docker-entrypoint-initdb.d) — a managed server (e.g. Azure Database for
PostgreSQL Flexible Server) has no such hook.

Run FIRST in the deploy migrate step — before `alembic upgrade head` (whose
GRANTs target this role) and before api/worker connect as it. Reads the role's
password from DMARC_APP_DB_PASSWORD.
"""

import asyncio
import logging
import os

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import settings
from app.db.session import async_session_factory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ensure_app_role")

_ROLE = "dmarc_app"
_ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"


async def main() -> None:
    password = os.environ.get("DMARC_APP_DB_PASSWORD")
    if not password:
        raise SystemExit("DMARC_APP_DB_PASSWORD is not set — cannot create the dmarc_app role")
    # standard_conforming_strings is on by default, so only single quotes need
    # doubling; the value is a deployment secret, not user input, but escape anyway.
    safe_pw = password.replace("'", "''")
    db_name = make_url(settings.database_url).database

    async with async_session_factory() as db:
        exists = (await db.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _ROLE})).scalar()
        if exists:
            await db.execute(text(f"ALTER ROLE {_ROLE} WITH {_ATTRS} PASSWORD '{safe_pw}'"))
            logger.info("role %s already exists — attributes/password refreshed", _ROLE)
        else:
            await db.execute(text(f"CREATE ROLE {_ROLE} {_ATTRS} PASSWORD '{safe_pw}'"))
            logger.info("created role %s", _ROLE)
        if db_name:
            await db.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {_ROLE}'))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
