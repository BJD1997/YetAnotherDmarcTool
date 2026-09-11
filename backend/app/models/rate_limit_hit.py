from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RateLimitHit(Base):
    """One row per rate-limited request, backing the optional Postgres shared
    rate-limit backend (RATE_LIMIT_BACKEND=postgres in
    app/services/auth/rate_limit.py) so the auth rate limit is correct across
    multiple api replicas. Worker/infra table: NOT row-level-security scoped,
    no organization_id column. Old rows are pruned by the rate_limit_prune
    background job (app/workers/scheduler.py)."""

    __tablename__ = "rate_limit_hits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # "<limiter-name>:<client-key>" (e.g. "login:203.0.113.10").
    bucket: Mapped[str] = mapped_column(String(320), nullable=False)
    hit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
