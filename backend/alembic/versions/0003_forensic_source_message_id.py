"""forensic report dedup key

Revision ID: 0003_forensic_source_message_id
Revises: 0002_domain_verification
Create Date: 2026-07-31

RUF reports have no natural per-report dedup key of their own (unlike
aggregate/TLS-RPT); adds source_message_id (the Graph message id) as one,
since a poll run retried after a partial failure could otherwise re-write
the same forensic report.
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_forensic_source_message_id"
down_revision = "0002_domain_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dmarc_forensic_reports", sa.Column("source_message_id", sa.String(255), nullable=True))
    op.execute(
        "UPDATE dmarc_forensic_reports SET source_message_id = id::text WHERE source_message_id IS NULL"
    )
    op.alter_column("dmarc_forensic_reports", "source_message_id", nullable=False)
    op.create_unique_constraint(
        "uq_dmarc_forensic_reports_source_message",
        "dmarc_forensic_reports",
        ["organization_id", "source_message_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_dmarc_forensic_reports_source_message", "dmarc_forensic_reports", type_="unique"
    )
    op.drop_column("dmarc_forensic_reports", "source_message_id")
