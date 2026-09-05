"""Per-client sliding-window rate limiter for the auth endpoints.

Two backends, chosen by `settings.rate_limit_backend`:

- **memory** (default) — a process-local sliding window. Correct when there's a
  single api process (one uvicorn worker per container, no `--workers`); state
  resets on restart, which only ever *forgives* counters, never wrongly blocks.
- **postgres** — a shared window in `rate_limit_hits`, so the limit is correct
  across multiple api replicas. Switch to this when scaling the api horizontally
  (see Production considerations in the README). Auth-endpoint volume is tiny, so
  the extra query per attempt is negligible — and cheaper than the Argon2 it gates.

Keyed on the real client IP, which is only trustworthy once the reverse-proxy
chain populates it correctly (FORWARDED_ALLOW_IPS, plus the edge's real-IP config).
Enforced as a pre-handler dependency, so it also caps how much Argon2 CPU a single
IP can burn (each login attempt is a deliberately expensive verify).
"""

import ipaddress
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status
from sqlalchemy import text

from app.config import settings
from app.db.session import async_session_factory


class _InMemoryWindow:
    """Process-local sliding window (deque of hit timestamps per key)."""

    def __init__(self, *, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                del self._hits[key]
                bucket = self._hits[key]
            if len(bucket) >= self.max_events:
                retry_after = int(self.window_seconds - (now - bucket[0])) + 1
                return False, max(retry_after, 1)
            bucket.append(now)
            return True, 0


async def _postgres_check(bucket: str, *, max_events: int, window_seconds: float) -> tuple[bool, int]:
    """Shared sliding window in `rate_limit_hits`. A transaction-scoped advisory
    lock on the bucket serializes concurrent checks for the same key across
    replicas, so the limit is exact rather than racy. All timing is done in the
    DB (no app/DB clock skew)."""
    async with async_session_factory() as db:
        async with db.begin():
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:b)::bigint)"), {"b": bucket})
            count = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM rate_limit_hits "
                        "WHERE bucket = :b AND hit_at > now() - make_interval(secs => :w)"
                    ),
                    {"b": bucket, "w": window_seconds},
                )
            ).scalar_one()
            if count >= max_events:
                retry_after = (
                    await db.execute(
                        text(
                            "SELECT ceil(extract(epoch FROM "
                            "(make_interval(secs => :w) - (now() - min(hit_at)))))::int "
                            "FROM rate_limit_hits WHERE bucket = :b AND hit_at > now() - make_interval(secs => :w)"
                        ),
                        {"b": bucket, "w": window_seconds},
                    )
                ).scalar()
                return False, max(int(retry_after or 1), 1)
            await db.execute(text("INSERT INTO rate_limit_hits (bucket, hit_at) VALUES (:b, now())"), {"b": bucket})
            return True, 0


class RateLimiter:
    """A named limiter that dispatches to the configured backend at call time
    (so RATE_LIMIT_BACKEND can be set per-deployment without code changes)."""

    def __init__(self, name: str, *, max_events: int, window_seconds: float) -> None:
        self.name = name
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._memory = _InMemoryWindow(max_events=max_events, window_seconds=window_seconds)

    async def check(self, key: str) -> tuple[bool, int]:
        if settings.rate_limit_backend == "postgres":
            return await _postgres_check(
                f"{self.name}:{key}", max_events=self.max_events, window_seconds=self.window_seconds
            )
        return self._memory.check(key)


def client_key(request: Request) -> str:
    """Bucket key from the client IP — the full address for IPv4, the /64 for
    IPv6 (a single client controls a whole /64, so limiting per-address would be
    trivially evaded)."""
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{host}/64", strict=False).network_address)
    return host


def rate_limiter(limiter: "RateLimiter"):
    """Build a FastAPI dependency enforcing `limiter` per client IP. Raises 429
    with a Retry-After header when the window is exhausted."""

    async def _dep(request: Request) -> None:
        allowed, retry_after = await limiter.check(client_key(request))
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many attempts — please wait and try again",
                headers={"Retry-After": str(retry_after)},
            )

    return _dep


async def prune_rate_limit_hits(max_age_seconds: int = 86400) -> None:
    """Delete rate_limit_hits older than any window cares about — run periodically
    by the `rate_limit_prune` background job. No-op when the memory backend is
    used (the table is simply empty)."""
    async with async_session_factory() as db:
        await db.execute(
            text("DELETE FROM rate_limit_hits WHERE hit_at < now() - make_interval(secs => :s)"),
            {"s": max_age_seconds},
        )
        await db.commit()


# Module-level singletons so every request shares one limiter. Generous enough
# that no human trips them, tight enough to make online brute force impractical
# and to bound per-IP Argon2 CPU. login is shared by the local and platform-admin
# password endpoints; otp by both verify-otp endpoints.
login_limiter = RateLimiter("login", max_events=10, window_seconds=300)
otp_limiter = RateLimiter("otp", max_events=10, window_seconds=300)
