from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.update_check_state import UpdateCheckState


async def get_or_create_update_check_state(db: AsyncSession) -> UpdateCheckState:
    result = await db.execute(select(UpdateCheckState).limit(1))
    state = result.scalar_one_or_none()
    if state is None:
        state = UpdateCheckState()
        db.add(state)
        await db.flush()
    return state
