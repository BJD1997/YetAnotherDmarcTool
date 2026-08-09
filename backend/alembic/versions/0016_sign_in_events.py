"""sign in events

Revision ID: 0016_sign_in_events
Revises: 0015_demo_read_only
Create Date: 2026-08-09

Per-org sign-in audit log — one row per completed sign-in attempt (success
or failure) across the Entra SSO callback, local password+TOTP, and TOTP
enrollment flows. organization_id is nullable and RLS-scoped like
job_runs: some failures (no Organization matches the Entra tenant, or a
local-login email that doesn't exist) have no org to attribute the row to,
and are simply invisible to every org's own view of this table — there's
no useful org to show them under anyway.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_sign_in_events"
down_revision = "0015_demo_read_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    sign_in_result = postgresql.ENUM("success", "failure", name="sign_in_result")
    sign_in_result.create(bind, checkfirst=True)
    sign_in_result.create_type = False

    # Already created by 0010_local_auth — reference it, don't recreate it.
    auth_method = postgresql.ENUM("entra", "local", name="auth_method", create_type=False)

    op.create_table(
        "sign_in_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempted_email", sa.String(255), nullable=True),
        sa.Column("auth_method", auth_method, nullable=False),
        sa.Column("result", sign_in_result, nullable=False),
        sa.Column("failure_reason", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sign_in_events_organization_id", "sign_in_events", ["organization_id"])
    op.create_index("ix_sign_in_events_created_at", "sign_in_events", ["created_at"])

    op.execute("ALTER TABLE sign_in_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sign_in_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON sign_in_events
        USING (
            organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR current_setting('app.is_platform_admin', true) = 'true'
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR current_setting('app.is_platform_admin', true) = 'true'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON sign_in_events")
    op.drop_table("sign_in_events")
    op.execute("DROP TYPE IF EXISTS sign_in_result")
