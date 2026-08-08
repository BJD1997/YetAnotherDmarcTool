"""local email+password+TOTP auth

Revision ID: 0010_local_auth
Revises: 0009_domain_mail_profile
Create Date: 2026-08-02

Lets an organization without an Entra tenant configured (entra_tenant_id
IS NULL) sign in via local email+password with mandatory TOTP instead of
being locked out entirely — see app/routers/auth.py's new /auth/local-login
et al. Existing users all get auth_method=entra (unchanged behavior);
entra_object_id becomes nullable since local-auth users don't have one.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_local_auth"
down_revision = "0009_domain_mail_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    auth_method = postgresql.ENUM("entra", "local", name="auth_method")
    auth_method.create(bind, checkfirst=True)
    auth_method.create_type = False

    op.add_column(
        "users", sa.Column("auth_method", auth_method, nullable=False, server_default="entra")
    )
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("otp_secret", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("otp_enrolled_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("users", "entra_object_id", existing_type=sa.String(64), nullable=True)

    # Local-auth login has no org context to disambiguate a lookup by
    # (unlike Entra, which resolves the org from the tid claim first), so
    # email must be unambiguous across orgs for local-auth accounts.
    op.execute(
        "CREATE UNIQUE INDEX uq_users_local_email ON users (lower(email)) WHERE auth_method = 'local'"
    )

    now = sa.func.now()

    op.create_table(
        "password_setup_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "mfa_pending_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_recovery_codes")
    op.drop_table("mfa_pending_challenges")
    op.drop_table("password_setup_tokens")
    op.execute("DROP INDEX IF EXISTS uq_users_local_email")
    op.alter_column("users", "entra_object_id", existing_type=sa.String(64), nullable=False)
    op.drop_column("users", "otp_enrolled_at")
    op.drop_column("users", "otp_secret")
    op.drop_column("users", "password_hash")
    op.drop_column("users", "auth_method")
    op.execute("DROP TYPE IF EXISTS auth_method")
