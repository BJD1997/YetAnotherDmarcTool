import uuid

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import OrganizationStatus, ReportSenderCheck, SpfAllQualifierMode
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


class Organization(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Entra (Azure AD) tenant ID this organization's users sign in with (dashboard
    # SSO) and whose mailbox we poll via Graph. Nullable until provisioning is
    # complete; once set, an Entra SSO login's `tid` claim is matched against it.
    entra_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True
    )

    status: Mapped[OrganizationStatus] = mapped_column(
        pg_enum(OrganizationStatus, "organization_status"),
        nullable=False,
        default=OrganizationStatus.active,
    )

    # Operator orgs' org_admins can access the platform-admin API surface
    # (/admin/*) through their normal session, no separate local-auth login
    # needed. Any number of orgs may be flagged (0026 dropped the original
    # one-org limit); set from the admin console. The local platform_admin
    # login (see PlatformAdmin) stays available as a break-glass fallback.
    is_operator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Client-configurable via Settings — see app/services/dns_checks/spf.py.
    spf_all_qualifier_mode: Mapped[SpfAllQualifierMode] = mapped_column(
        pg_enum(SpfAllQualifierMode, "spf_all_qualifier_mode"),
        nullable=False,
        default=SpfAllQualifierMode.strict,
    )

    # How strictly incoming reports are checked for forgery; changing it
    # re-evaluates reports already received (Settings, org admins).
    report_sender_check: Mapped[ReportSenderCheck] = mapped_column(
        pg_enum(ReportSenderCheck, "report_sender_check"),
        nullable=False,
        default=ReportSenderCheck.standard,
    )

    # Explicit opt-in for orgs that COULD set up their own MailboxConnection
    # (entra_tenant_id is set) but want the operator-hosted address instead.
    # Irrelevant for local-auth orgs (entra_tenant_id is None) — those have
    # no Entra tenant to grant Mail Access consent from, so hosted mailbox
    # is always available to them regardless of this flag — see
    # app/routers/domains.py's _hosted_mailbox_available.
    hosted_mailbox_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # How far back each domain's grade, failing-message count, readiness and
    # the Senders list's default look (Settings, org admins): 30/60/90/180.
    # Ask AI buttons (Settings → General, the onboarding wizard, or a one-time
    # question for org admins): None = not answered yet (the question shows),
    # False = declined, True = on. See app/services/ask_ai.
    ask_ai_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    rating_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90, server_default="90")

    # Blocks every state-changing request for this org's users — see
    # enforce_demo_read_only in app/main.py. Defaults False so this can
    # never affect a real org by accident; the one intended use is a
    # published public demo login where visitors can look around but can't
    # actually change anything.
    is_demo_read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
