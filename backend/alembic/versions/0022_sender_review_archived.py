"""add 'archived' to sender_review_status

New SenderReviewStatus value for retiring a sender that's no longer in use
(a decommissioned host, a dropped ESP) — kept for history but hidden from the
active sender inventory. Same additive pattern as 0006_starttls_check_type.

Revision ID: 0022_sender_review_archived
Revises: 0021_fcrdns_source_ip_identities
"""

from alembic import op

revision = "0022_sender_review_archived"
down_revision = "0021_fcrdns_source_ip_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safe inside a transaction on PG12+, as long as the new value isn't used
    # in the same transaction (it isn't — this only adds the label).
    op.execute("ALTER TYPE sender_review_status ADD VALUE IF NOT EXISTS 'archived'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE — removing an enum label
    # requires rebuilding the type, not attempted here since this migration
    # only adds a label. Any rows set to 'archived' would need remapping first.
    pass
