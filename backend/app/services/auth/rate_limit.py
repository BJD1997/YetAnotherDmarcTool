"""In-memory per-client sliding-window rate limiter for the auth endpoints.

Single-process by design: each api container runs exactly one uvicorn process
(see docker-compose.yml's command — no --workers), so a process-local store is
correct for this deployment. State is per-process and resets on restart, which
only ever *forgives* counters, never wrongly blocks — an acceptable tradeoff
here. If this app is ever scaled to multiple api replicas or uvicorn workers,
move this to a shared store (Redis/Postgres) or each replica will only count
its own slice of traffic.

Keyed on the real client IP, which is only trustworthy once the reverse-proxy
chain populates it correctly (FORWARDED_ALLOW_IPS in docker-compose.yml, plus
NPM's set_real_ip_from/CF-Connecting-IP config). Without that, every visitor
behind one Cloudflare edge IP would share a single bucket. Enforced as a
pre-handler dependency, so it also caps how much Argon2 CPU a single IP can
burn (each login attempt is a deliberately expensive verify).
"""

import ipaddress
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(self, *, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, key: str, now: float) -> None:
        cutoff = now - self.window_seconds
        bucket = self._hits[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if not bucket:
            # Don't let idle keys accumulate forever — defaultdict re-creates
            # the deque on the next hit for this key anyway.
            del self._hits[key]

    def check(self, key: str) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Records the hit when allowed."""
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            bucket = self._hits[key]
            if len(bucket) >= self.max_events:
                retry_after = int(self.window_seconds - (now - bucket[0])) + 1
                return False, max(retry_after, 1)
            bucket.append(now)
            return True, 0


def client_key(request: Request) -> str:
    """Bucket key from the client IP — the full address for IPv4, the /64 for
    IPv6 (a single client controls a whole /64, so limiting per-address would
    be trivially evaded)."""
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{host}/64", strict=False).network_address)
    return host


def rate_limiter(limiter: "SlidingWindowLimiter"):
    """Build a FastAPI dependency enforcing `limiter` per client IP. Raises 429
    with a Retry-After header when the window is exhausted."""

    async def _dep(request: Request) -> None:
        allowed, retry_after = limiter.check(client_key(request))
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many attempts — please wait and try again",
                headers={"Retry-After": str(retry_after)},
            )

    return _dep


# Module-level singletons so every request shares one store. Generous enough
# that no human trips them, tight enough to make online brute force impractical
# and to bound per-IP Argon2 CPU. login_limiter is shared by the local and
# platform-admin password endpoints; otp_limiter by both verify-otp endpoints.
login_limiter = SlidingWindowLimiter(max_events=10, window_seconds=300)
otp_limiter = SlidingWindowLimiter(max_events=10, window_seconds=300)
