"""Sign-in with Google Workspace or an organization's own OIDC provider

An organization has local accounts and at most one sign-in provider:
Microsoft (entra_tenant_id, as before), Google Workspace
(google_workspace_domain) or its own OpenID Connect provider (oidc_*).
slug is the short name in its direct sign-in link, /login/<slug>.
Users from Google or OIDC are keyed by the provider's stable user ID (sub)
in external_subject; Microsoft users keep entra_object_id.

Revision ID: 0043_sign_in_providers
Revises: 0042_imap_mailbox
"""

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0043_sign_in_providers"
down_revision = "0042_imap_mailbox"
branch_labels = None
depends_on = None


def _slugify(name: str) -> str:
    # A copy of app/services/organizations/slug.py: migrations don't import app code.
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:63].rstrip("-")
    return slug or "org"


def upgrade() -> None:
    op.execute("ALTER TYPE auth_method ADD VALUE IF NOT EXISTS 'google'")
    op.execute("ALTER TYPE auth_method ADD VALUE IF NOT EXISTS 'oidc'")

    op.add_column("organizations", sa.Column("google_workspace_domain", sa.String(253), nullable=True))
    op.create_unique_constraint("uq_organizations_google_workspace_domain", "organizations", ["google_workspace_domain"])
    op.add_column("organizations", sa.Column("oidc_issuer", sa.String(2048), nullable=True))
    op.add_column("organizations", sa.Column("oidc_client_id", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("oidc_client_secret", sa.String(1024), nullable=True))
    op.create_check_constraint(
        "ck_organizations_one_sign_in_provider",
        "organizations",
        "num_nonnulls(entra_tenant_id, google_workspace_domain, oidc_issuer) <= 1",
    )
    op.create_check_constraint(
        "ck_organizations_oidc_complete",
        "organizations",
        "(oidc_issuer IS NULL) = (oidc_client_id IS NULL) AND (oidc_issuer IS NULL) = (oidc_client_secret IS NULL)",
    )

    op.add_column("organizations", sa.Column("slug", sa.String(63), nullable=True))
    bind = op.get_bind()
    taken: set[str] = set()
    for org_id, name in bind.execute(sa.text("SELECT id, name FROM organizations ORDER BY created_at, id")).all():
        base = _slugify(name)
        slug, n = base, 2
        while slug in taken:
            suffix = f"-{n}"
            slug = base[: 63 - len(suffix)] + suffix
            n += 1
        taken.add(slug)
        bind.execute(sa.text("UPDATE organizations SET slug = :slug WHERE id = :id"), {"slug": slug, "id": org_id})
    op.alter_column("organizations", "slug", nullable=False)
    op.create_unique_constraint("uq_organizations_slug", "organizations", ["slug"])

    op.add_column("users", sa.Column("external_subject", sa.String(255), nullable=True))
    op.create_unique_constraint(
        "uq_users_org_method_external_subject", "users", ["organization_id", "auth_method", "external_subject"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_org_method_external_subject", "users")
    op.drop_column("users", "external_subject")
    op.drop_constraint("uq_organizations_slug", "organizations")
    op.drop_column("organizations", "slug")
    op.drop_constraint("ck_organizations_oidc_complete", "organizations")
    op.drop_constraint("ck_organizations_one_sign_in_provider", "organizations")
    for column in ("oidc_client_secret", "oidc_client_id", "oidc_issuer"):
        op.drop_column("organizations", column)
    op.drop_constraint("uq_organizations_google_workspace_domain", "organizations")
    op.drop_column("organizations", "google_workspace_domain")
    # Postgres can't drop enum values; google/oidc stay in auth_method, unused.
