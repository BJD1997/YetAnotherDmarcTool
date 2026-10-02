"""account-change audit events; drop unused custom-app columns

- sign_in_result gains 'account_change' and sign_in_events gains
  actor_email, so password/MFA changes and admin resets land in the same
  Sign-in activity log (who did what to whom).
- Drops mailbox_connections.uses_custom_app / custom_client_id /
  custom_client_secret_encrypted — a bring-your-own-Entra-app escape hatch
  that was never wired up to anything and held no data.

Revision ID: 0027_account_audit
Revises: 0026_multiple_operator_orgs
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_account_audit"
down_revision = "0026_multiple_operator_orgs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safe inside the migration transaction on PG12+ since nothing here
    # writes an 'account_change' row (see 0006 for the same pattern).
    op.execute("ALTER TYPE sign_in_result ADD VALUE IF NOT EXISTS 'account_change'")
    op.add_column("sign_in_events", sa.Column("actor_email", sa.String(255), nullable=True))

    op.drop_column("mailbox_connections", "custom_client_secret_encrypted")
    op.drop_column("mailbox_connections", "custom_client_id")
    op.drop_column("mailbox_connections", "uses_custom_app")


def downgrade() -> None:
    op.add_column(
        "mailbox_connections",
        sa.Column("uses_custom_app", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column("mailbox_connections", sa.Column("custom_client_id", sa.String(64), nullable=True))
    op.add_column("mailbox_connections", sa.Column("custom_client_secret_encrypted", sa.LargeBinary, nullable=True))
    op.drop_column("sign_in_events", "actor_email")
    # The 'account_change' enum label stays — Postgres can't drop one (see 0006).
