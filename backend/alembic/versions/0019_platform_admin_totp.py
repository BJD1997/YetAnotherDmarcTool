"""platform admin totp

Revision ID: 0019_platform_admin_totp
Revises: 0018_update_check_prereleases
Create Date: 2026-08-10

Platform admin accounts have had no MFA of any kind since this app's
first commit — only local-auth org users did (see 0010_local_auth).
Given platform admins can provision/delete organizations and trigger
updates, mirrors that same mandatory TOTP + recovery-code design here,
as its own tables (own FK target, own id space) rather than reusing the
org-user ones.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0019_platform_admin_totp"
down_revision = "0018_update_check_prereleases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("platform_admins", sa.Column("otp_secret", sa.String(64), nullable=True))
    op.add_column("platform_admins", sa.Column("otp_enrolled_at", sa.DateTime(timezone=True), nullable=True))

    now = sa.func.now()

    op.create_table(
        "platform_admin_mfa_pending_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "platform_admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_admins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "platform_admin_recovery_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "platform_admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_admins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_admin_recovery_codes")
    op.drop_table("platform_admin_mfa_pending_challenges")
    op.drop_column("platform_admins", "otp_enrolled_at")
    op.drop_column("platform_admins", "otp_secret")
