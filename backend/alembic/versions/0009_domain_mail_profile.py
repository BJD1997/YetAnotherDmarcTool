"""domain mail profile

Revision ID: 0009_domain_mail_profile
Revises: 0008_sender_reviews
Create Date: 2026-08-02

Lets a domain be marked receive_only or parked (no mail sent or received)
so the DMARC policy recommendation can skip straight to p=reject/np=reject
instead of waiting on report volume/pass-rate that will never arrive for a
domain with no legitimate outbound mail — see
_build_base_recommendation in app/routers/dmarc_reports.py.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_domain_mail_profile"
down_revision = "0008_sender_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    domain_mail_profile = postgresql.ENUM(
        "sends_mail", "receive_only", "parked", name="domain_mail_profile"
    )
    domain_mail_profile.create(bind, checkfirst=True)
    domain_mail_profile.create_type = False

    op.add_column(
        "domains",
        sa.Column("mail_profile", domain_mail_profile, nullable=False, server_default="sends_mail"),
    )


def downgrade() -> None:
    op.drop_column("domains", "mail_profile")
    op.execute("DROP TYPE IF EXISTS domain_mail_profile")
