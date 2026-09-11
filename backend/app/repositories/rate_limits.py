"""Queries backing the Postgres-shared rate-limit backend (rate_limit_hits).
See app/services/auth/rate_limit.py for orchestration (bucket-key building,
backend selection) that calls these."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def acquire_bucket_lock(db: AsyncSession, bucket: str) -> None:
    """Transaction-scoped advisory lock serializing concurrent checks for the
    same bucket across replicas, so the count-then-insert the caller performs
    next is exact rather than racy. Must be called inside the same
    transaction as that count/insert — releases automatically at transaction
    end (pg_advisory_xact_lock, not pg_advisory_lock)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:b)::bigint)"), {"b": bucket})


async def count_recent_hits(db: AsyncSession, bucket: str, window_seconds: float) -> int:
    return (
        await db.execute(
            text(
                "SELECT count(*) FROM rate_limit_hits "
                "WHERE bucket = :b AND hit_at > now() - make_interval(secs => :w)"
            ),
            {"b": bucket, "w": window_seconds},
        )
    ).scalar_one()


async def earliest_hit_retry_after(db: AsyncSession, bucket: str, window_seconds: float) -> int | None:
    return (
        await db.execute(
            text(
                "SELECT ceil(extract(epoch FROM "
                "(make_interval(secs => :w) - (now() - min(hit_at)))))::int "
                "FROM rate_limit_hits WHERE bucket = :b AND hit_at > now() - make_interval(secs => :w)"
            ),
            {"b": bucket, "w": window_seconds},
        )
    ).scalar()


async def record_hit(db: AsyncSession, bucket: str) -> None:
    await db.execute(text("INSERT INTO rate_limit_hits (bucket, hit_at) VALUES (:b, now())"), {"b": bucket})


async def prune_hits_older_than(db: AsyncSession, max_age_seconds: int) -> None:
    await db.execute(
        text("DELETE FROM rate_limit_hits WHERE hit_at < now() - make_interval(secs => :s)"),
        {"s": max_age_seconds},
    )
