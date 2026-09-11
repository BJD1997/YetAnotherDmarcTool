# backend/tests/services/auth/test_rate_limit.py
"""Rate-limiter tests. The in-memory backend test needs no database (runs
everywhere); the Postgres shared-window test is gated on TEST_DATABASE_URL.
"""

from app.services.auth import rate_limit as rl


async def test_memory_backend_enforces_window():
    # Default backend is "memory"; no DB involved.
    limiter = rl.RateLimiter("mem", max_events=2, window_seconds=100)
    assert (await limiter.check("k"))[0] is True
    assert (await limiter.check("k"))[0] is True
    allowed, retry = await limiter.check("k")
    assert allowed is False
    assert retry >= 1
    # A different key has its own bucket.
    assert (await limiter.check("other"))[0] is True


async def test_postgres_backend_shares_window_across_sessions(app_sessionmaker, monkeypatch):
    # Each check opens its own session (as multiple api replicas would) — the
    # shared rate_limit_hits table must still enforce one window.
    monkeypatch.setattr(rl.settings, "rate_limit_backend", "postgres")
    monkeypatch.setattr(rl, "async_session_factory", app_sessionmaker)
    limiter = rl.RateLimiter("t", max_events=3, window_seconds=60)

    for _ in range(3):
        allowed, _ = await limiter.check("1.2.3.4")
        assert allowed is True
    allowed, retry = await limiter.check("1.2.3.4")
    assert allowed is False
    assert retry >= 1

    # Independent key, independent window.
    assert (await limiter.check("5.6.7.8"))[0] is True
