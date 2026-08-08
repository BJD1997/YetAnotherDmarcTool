"""remove pending_setup organization status

Revision ID: 0007_remove_pending_setup_status
Revises: 0006_starttls_check_type
Create Date: 2026-08-01

Organizations now only ever sit in `active` or `suspended` — pending_setup
never actually gated anything: an org with no entra_tenant_id yet simply
can't receive a matching SSO callback regardless of its status label, so
it was just an extra state to reason about for no functional benefit.
Postgres has no ALTER TYPE ... DROP VALUE, so this recreates the enum
with only the two remaining values (the standard safe pattern).
"""

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_remove_pending_setup_status"
down_revision = "0006_starttls_check_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Defensive: no live rows are in pending_setup as of writing this
    # migration, but don't assume that still holds whenever it actually runs.
    op.execute("UPDATE organizations SET status = 'active' WHERE status = 'pending_setup'")
    op.execute("ALTER TABLE organizations ALTER COLUMN status DROP DEFAULT")

    new_status = postgresql.ENUM("active", "suspended", name="organization_status_new")
    new_status.create(bind, checkfirst=True)

    op.execute(
        "ALTER TABLE organizations ALTER COLUMN status TYPE organization_status_new "
        "USING status::text::organization_status_new"
    )

    op.execute("DROP TYPE organization_status")
    op.execute("ALTER TYPE organization_status_new RENAME TO organization_status")
    op.execute("ALTER TABLE organizations ALTER COLUMN status SET DEFAULT 'active'")


def downgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TABLE organizations ALTER COLUMN status DROP DEFAULT")

    old_status = postgresql.ENUM("pending_setup", "active", "suspended", name="organization_status_old")
    old_status.create(bind, checkfirst=True)

    op.execute(
        "ALTER TABLE organizations ALTER COLUMN status TYPE organization_status_old "
        "USING status::text::organization_status_old"
    )

    op.execute("DROP TYPE organization_status")
    op.execute("ALTER TYPE organization_status_old RENAME TO organization_status")
    op.execute("ALTER TABLE organizations ALTER COLUMN status SET DEFAULT 'pending_setup'")
