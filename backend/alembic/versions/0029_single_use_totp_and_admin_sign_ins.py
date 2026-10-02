"""single-use TOTP codes; break-glass sign-ins in the sign-in log

- users/platform_admins.otp_last_used_step: the time step of the last TOTP
  code accepted at sign-in, so a code can't be used twice.
- auth_method gains 'platform_admin', used only on sign_in_events rows for
  break-glass admin sign-ins (organization_id NULL — shown in the admin
  console, invisible to every organization).

Revision ID: 0029_single_use_totp
Revises: 0028_report_sender_trust
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_single_use_totp"
down_revision = "0028_report_sender_trust"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("otp_last_used_step", sa.BigInteger, nullable=True))
    op.add_column("platform_admins", sa.Column("otp_last_used_step", sa.BigInteger, nullable=True))
    # Safe in the migration transaction on PG12+: nothing here writes the new
    # label (see 0006).
    op.execute("ALTER TYPE auth_method ADD VALUE IF NOT EXISTS 'platform_admin'")


def downgrade() -> None:
    op.drop_column("platform_admins", "otp_last_used_step")
    op.drop_column("users", "otp_last_used_step")
    # The 'platform_admin' enum label stays — Postgres can't drop one (see 0006).
