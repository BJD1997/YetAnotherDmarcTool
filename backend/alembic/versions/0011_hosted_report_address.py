"""hosted report address

Revision ID: 0011_hosted_report_address
Revises: 0010_local_auth
Create Date: 2026-08-02

<random>+reports@<hosted domain> — an operator-hosted DMARC rua= address
for customers with no mailbox of their own to dedicate, polled by
app/workers/jobs/hosted_reports_poll_job.py. See POST
/domains/{id}/hosted-report-address in app/routers/domains.py.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_hosted_report_address"
down_revision = "0010_local_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("domains", sa.Column("hosted_report_address", sa.String(320), nullable=True))
    op.create_unique_constraint("uq_domains_hosted_report_address", "domains", ["hosted_report_address"])

    # sync_status already exists (created by the initial migration for
    # mailbox_connections) — reused here rather than a second identical enum.
    op.create_table(
        "hosted_reports_poll_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("delta_link", sa.Text, nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", postgresql.ENUM("success", "error", name="sync_status", create_type=False), nullable=True),
        sa.Column("last_sync_error", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("hosted_reports_poll_state")
    op.drop_constraint("uq_domains_hosted_report_address", "domains", type_="unique")
    op.drop_column("domains", "hosted_report_address")
