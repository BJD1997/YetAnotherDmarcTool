"""per-organization report sender check; store the facts behind it

Each report now keeps the facts its sender check is based on — where the
email came from (sender_origin), its DMARC result (sender_dmarc), and whether
the sender is the reporter the report names (sender_matches_reporter) — and
sender_verified is derived from them under the organization's
report_sender_check setting, so changing the setting re-evaluates reports
already received.

Backfill: rows verified under the previous rule (DMARC pass from outside)
get those facts. No row was left out under it (checked on the live
instances), so nothing else needs mapping; unchecked rows stay NULL.

Revision ID: 0030_report_sender_check
Revises: 0029_single_use_totp
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_report_sender_check"
down_revision = "0029_single_use_totp"
branch_labels = None
depends_on = None

_REPORT_TABLES = ("dmarc_aggregate_reports", "dmarc_forensic_reports", "tls_rpt_reports")


def upgrade() -> None:
    mode = postgresql.ENUM("standard", "strict", "off", name="report_sender_check")
    mode.create(op.get_bind(), checkfirst=True)
    mode.create_type = False
    op.add_column(
        "organizations", sa.Column("report_sender_check", mode, nullable=False, server_default="standard")
    )

    for table in _REPORT_TABLES:
        op.add_column(table, sa.Column("sender_origin", sa.String(16), nullable=True))
        op.add_column(table, sa.Column("sender_dmarc", sa.String(32), nullable=True))
        op.add_column(table, sa.Column("sender_matches_reporter", sa.Boolean, nullable=True))
        op.execute(
            f"UPDATE {table} SET sender_origin = 'external', sender_dmarc = 'pass' WHERE sender_verified IS TRUE"
        )
        # Anything the old rule left out has no recorded facts to re-evaluate:
        # count it again, like every other unchecked report.
        op.execute(f"UPDATE {table} SET sender_verified = NULL WHERE sender_verified IS FALSE")
    op.execute(
        "UPDATE dmarc_aggregate_records r SET sender_verified = NULL WHERE sender_verified IS FALSE"
    )


def downgrade() -> None:
    for table in _REPORT_TABLES:
        op.drop_column(table, "sender_matches_reporter")
        op.drop_column(table, "sender_dmarc")
        op.drop_column(table, "sender_origin")
    op.drop_column("organizations", "report_sender_check")
    op.execute("DROP TYPE IF EXISTS report_sender_check")
