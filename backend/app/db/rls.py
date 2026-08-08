from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# CAVEAT: `set_config(..., is_local=true)` (like `SET LOCAL`) only lasts for
# the current transaction. SQLAlchemy's AsyncSession auto-begins a new
# transaction lazily after a commit — so if a route calls `db.commit()`
# mid-request and then runs more RLS-protected queries afterward, the
# context set here is gone and every row will be (safely, but confusingly)
# hidden. Call set_org_context()/set_platform_admin_context() again after
# any mid-request commit, or simply do all RLS-protected work before the
# one commit at the end of the request (the pattern used everywhere so far).


async def set_org_context(db: AsyncSession, organization_id) -> None:
    """SET LOCAL app.current_org_id for the current transaction, via
    set_config() so the value can be bound as a parameter rather than
    string-interpolated into SQL. See the RLS policies in
    alembic/versions/0001_initial_schema.py."""
    await db.execute(
        text("SELECT set_config('app.current_org_id', :val, true)"), {"val": str(organization_id)}
    )


async def set_platform_admin_context(db: AsyncSession, *, is_admin: bool) -> None:
    await db.execute(
        text("SELECT set_config('app.is_platform_admin', :val, true)"),
        {"val": "true" if is_admin else "false"},
    )
