"""report sender trust

Records whether the email carrying each report passed DMARC for its own
sender (sender_verified) and which domain that was (sender_domain), so a
forged report — anyone can mail a reporting address — is kept but left out
of every stat, and can't block the genuine copy via the dedup key. Existing
rows stay NULL ("not checked") and keep counting as before.

Revision ID: 0028_report_sender_trust
Revises: 0027_account_audit
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_report_sender_trust"
down_revision = "0027_account_audit"
branch_labels = None
depends_on = None

_REPORT_TABLES = ("dmarc_aggregate_reports", "dmarc_forensic_reports", "tls_rpt_reports")


def upgrade() -> None:
    for table in _REPORT_TABLES:
        op.add_column(table, sa.Column("sender_verified", sa.Boolean, nullable=True))
        op.add_column(table, sa.Column("sender_domain", sa.String(255), nullable=True))
    op.add_column("dmarc_aggregate_records", sa.Column("sender_verified", sa.Boolean, nullable=True))


def downgrade() -> None:
    op.drop_column("dmarc_aggregate_records", "sender_verified")
    for table in _REPORT_TABLES:
        op.drop_column(table, "sender_domain")
        op.drop_column(table, "sender_verified")
