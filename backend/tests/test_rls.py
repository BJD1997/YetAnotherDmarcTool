"""Integration tests for Postgres row-level security — the actual multi-tenancy
boundary. These run as the non-owner `dmarc_app` role (the one FORCE ROW LEVEL
SECURITY binds, and the one the running app connects as), so they prove the same
isolation that protects production, not a mock of it.

Gated on TEST_DATABASE_URL (see conftest.py); skipped when it's unset.
"""

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.rls import set_org_context, set_platform_admin_context
from app.models.domain import Domain
from app.models.organization import Organization


async def _seed_two_orgs(owner, app):
    """Two orgs (created by the RLS-exempt owner), each with one domain inserted
    by the app role under that org's own context. Returns (org_a, org_b)."""
    org_a = Organization(name="Org A")
    org_b = Organization(name="Org B")
    owner.add_all([org_a, org_b])
    await owner.commit()

    await set_org_context(app, org_a.id)
    app.add(Domain(organization_id=org_a.id, name="a.example.com"))
    await app.commit()

    await set_org_context(app, org_b.id)
    app.add(Domain(organization_id=org_b.id, name="b.example.com"))
    await app.commit()
    return org_a, org_b


async def test_org_context_scopes_reads(rls_sessions):
    owner, app = rls_sessions
    org_a, org_b = await _seed_two_orgs(owner, app)

    await set_org_context(app, org_a.id)
    seen_a = {d.name for d in (await app.execute(select(Domain))).scalars()}
    await app.rollback()
    assert seen_a == {"a.example.com"}  # org A cannot see org B's domain

    await set_org_context(app, org_b.id)
    seen_b = {d.name for d in (await app.execute(select(Domain))).scalars()}
    await app.rollback()
    assert seen_b == {"b.example.com"}


async def test_no_context_hides_all_rows(rls_sessions):
    owner, app = rls_sessions
    await _seed_two_orgs(owner, app)

    # A fresh transaction with neither app.current_org_id nor is_platform_admin
    # set — the policy matches nothing, so a leaked/unset context fails closed.
    rows = (await app.execute(select(Domain))).scalars().all()
    await app.rollback()
    assert rows == []


async def test_platform_admin_context_bypasses_isolation(rls_sessions):
    owner, app = rls_sessions
    await _seed_two_orgs(owner, app)

    await set_platform_admin_context(app, is_admin=True)
    seen = {d.name for d in (await app.execute(select(Domain))).scalars()}
    await app.rollback()
    assert seen == {"a.example.com", "b.example.com"}


async def test_cross_tenant_write_is_rejected(rls_sessions):
    owner, app = rls_sessions
    org_a, org_b = await _seed_two_orgs(owner, app)

    # Under org A's context, try to write a row owned by org B — the policy's
    # WITH CHECK must refuse it rather than let A plant data in B.
    await set_org_context(app, org_a.id)
    app.add(Domain(organization_id=org_b.id, name="cross-tenant.example.com"))
    try:
        raised = False
        await app.commit()
    except DBAPIError:
        raised = True
    finally:
        await app.rollback()
    assert raised, "cross-tenant INSERT should violate the RLS WITH CHECK policy"


# Deliberately NOT RLS-scoped despite carrying an organization_id (see the
# TENANT_SCOPED_TABLES comment in 0001_initial_schema.py and app/models/session.py):
# sessions are looked up by opaque token BEFORE any org context is known, so an
# org policy would break authentication itself — and the token is the secret, so
# there's nothing to isolate by org. Any table added here needs that justification.
RLS_EXEMPT_ORG_TABLES = {"user_sessions", "platform_admin_sessions"}


async def test_every_org_scoped_table_has_forced_rls(rls_sessions):
    """PR-safety guard: any table with an organization_id column MUST have
    ENABLE + FORCE row-level security and a policy (unless explicitly listed in
    RLS_EXEMPT_ORG_TABLES with a reason). A new tenant-owned table that forgets
    its RLS policy — the exact mistake easy to make in a PR — fails here instead
    of silently shipping a tenant-isolation hole."""
    owner, _app = rls_sessions
    rows = (
        await owner.execute(
            text(
                """
                SELECT c.relname AS relname,
                       c.relrowsecurity AS rls_enabled,
                       c.relforcerowsecurity AS rls_forced,
                       EXISTS (
                           SELECT 1 FROM pg_policies p
                           WHERE p.schemaname = 'public' AND p.tablename = c.relname
                       ) AS has_policy
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND EXISTS (
                      SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema = 'public'
                        AND col.table_name = c.relname
                        AND col.column_name = 'organization_id'
                  )
                """
            )
        )
    ).all()

    assert rows, "expected at least one org-scoped table"
    offenders = [
        r.relname
        for r in rows
        if r.relname not in RLS_EXEMPT_ORG_TABLES and not (r.rls_enabled and r.rls_forced and r.has_policy)
    ]
    assert offenders == [], f"org-scoped tables missing FORCE RLS + policy: {offenders}"
