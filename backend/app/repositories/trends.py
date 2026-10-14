import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import Domain
from app.models.domain_trend import DomainTrend
from app.services.rating.trend import TrendResult


async def upsert_trend(db: AsyncSession, domain: Domain, result: TrendResult) -> None:
    values = {
        "domain_id": domain.id,
        "organization_id": domain.organization_id,
        "state": result.state,
        "recent_pass_pct": result.recent_pass_pct,
        "baseline_pass_pct": result.baseline_pass_pct,
        "recent_messages": result.recent_messages,
        "baseline_messages": result.baseline_messages,
        "days_below": result.days_below,
        "days_above": result.days_above,
        "computed_at": datetime.now(timezone.utc),
    }
    stmt = pg_insert(DomainTrend).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["domain_id"], set_={k: v for k, v in values.items() if k != "domain_id"}
    )
    await db.execute(stmt)


async def get_trend(db: AsyncSession, domain_id: uuid.UUID) -> DomainTrend | None:
    return (await db.execute(select(DomainTrend).where(DomainTrend.domain_id == domain_id))).scalar_one_or_none()
