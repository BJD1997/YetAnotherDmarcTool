from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db import report_trust  # noqa: F401 — registers the unverified-report filter on every session

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Optional read-replica engine, built only if configured — falls back to the
# primary otherwise (see get_read_db below). A streaming replica physically
# rejects writes, so any accidental write through this session fails loudly
# with a clear Postgres error rather than silently succeeding unexpectedly.
_read_engine = (
    create_async_engine(
        settings.database_read_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    if settings.database_read_url
    else None
)
_read_session_factory = async_sessionmaker(_read_engine, expire_on_commit=False) if _read_engine else async_session_factory


async def assert_rls_enforced() -> None:
    """Refuses to start if either connection's role is exempt from row-level
    security. set_org_context still "succeeds" on such a connection, so a
    misconfigured DATABASE_URL/DATABASE_READ_URL would otherwise silently
    show every tenant's data to every other tenant. (Table owners are fine —
    the migrations FORCE ROW LEVEL SECURITY — only superusers and BYPASSRLS
    roles skip it.)"""
    engines = {"DATABASE_URL": engine}
    if _read_engine is not None:
        engines["DATABASE_READ_URL"] = _read_engine
    for setting_name, eng in engines.items():
        async with eng.connect() as conn:
            role = (
                await conn.execute(
                    text("SELECT current_user AS name, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()
        if role.rolsuper or role.rolbypassrls:
            raise RuntimeError(
                f"{setting_name} connects as '{role.name}', which bypasses row-level security — tenant isolation "
                "would be silently off. Connect as the non-owner dmarc_app role instead."
            )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    """Same shape as get_db, routed to the optional read-replica engine when
    DATABASE_READ_URL is configured, or the primary otherwise — invisible to
    a deployment with no replica configured. Used only by report/analytics GET
    endpoints where a brief replica-lag window is acceptable; anything where a
    user might expect to see their own just-made write reflected stays on
    get_db. RLS's SET LOCAL context-setting works identically on a replica
    session — it's the same schema and policies as the primary."""
    async with _read_session_factory() as session:
        yield session
