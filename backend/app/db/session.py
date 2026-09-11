from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Optional read-replica engine, built only if configured — falls back to the
# primary otherwise (see get_read_db below). A streaming replica physically
# rejects writes, so any accidental write through this session fails loudly
# with a clear Postgres error rather than silently succeeding unexpectedly.
_read_engine = create_async_engine(settings.database_read_url, pool_pre_ping=True) if settings.database_read_url else None
_read_session_factory = async_sessionmaker(_read_engine, expire_on_commit=False) if _read_engine else async_session_factory


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
