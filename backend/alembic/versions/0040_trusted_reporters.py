"""trusted reporters; reports only count from known reporters

Passing DMARC as the reporter a report names only proves who sent it —
anyone can register a domain and send "reports" from it. From now on the
sender check (standard and strict) also requires the sending domain to be a
known reporter: a built-in list of the large providers' report systems, or
one the organization trusts (this table). Normal RLS.

Backfill: re-evaluates stored reports under the new rule. The table starts
empty, so the only change is counted rows whose sending domain isn't on the
built-in list — frozen here as it was when this ran — becoming left out.
Unchecked rows (no sender facts) and organizations on "off" are untouched.

Revision ID: 0040_trusted_reporters
Revises: 0039_onboarding_completed
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0040_trusted_reporters"
down_revision = "0039_onboarding_completed"
branch_labels = None
depends_on = None

_REPORT_TABLES = ("dmarc_aggregate_reports", "dmarc_forensic_reports", "tls_rpt_reports")
_KNOWN_REPORTERS = ("corp.mail.ru", "dmarc.yahoo.com", "google.com", "microsoft.com")


def upgrade() -> None:
    op.create_table(
        "trusted_reporters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column(
            "trusted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "domain", name="uq_trusted_reporters_org_domain"),
    )

    op.execute("ALTER TABLE trusted_reporters ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trusted_reporters FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON trusted_reporters
        USING (
            organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR current_setting('app.is_platform_admin', true) = 'true'
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR current_setting('app.is_platform_admin', true) = 'true'
        )
        """
    )

    known = ", ".join(f"'{d}'" for d in _KNOWN_REPORTERS)
    for table in _REPORT_TABLES:
        op.execute(
            f"""
            UPDATE {table} r SET sender_verified = FALSE
            FROM organizations o
            WHERE o.id = r.organization_id AND o.report_sender_check <> 'off'
              AND r.sender_verified IS TRUE AND r.sender_origin IS NOT NULL
              AND (r.sender_domain IS NULL OR r.sender_domain NOT IN ({known}))
            """
        )
    op.execute(
        """
        UPDATE dmarc_aggregate_records rec SET sender_verified = FALSE
        FROM dmarc_aggregate_reports rep
        WHERE rep.id = rec.report_id AND rep.sender_verified IS FALSE AND rec.sender_verified IS TRUE
        """
    )


def downgrade() -> None:
    # Reports left out by the known-reporter rule stay left out until the
    # organization's setting is next changed (which re-evaluates them).
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON trusted_reporters")
    op.drop_table("trusted_reporters")
