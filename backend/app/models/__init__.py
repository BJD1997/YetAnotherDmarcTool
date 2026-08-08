"""Importing this package registers every model on Base.metadata, which
alembic/env.py needs for autogenerate and which the initial migration's
hand-written DDL is kept in sync with."""

from app.models.dkim_selector import DkimSelector
from app.models.dismissed_detected_domain import DismissedDetectedDomain
from app.models.dmarc_aggregate import DmarcAggregateRecord, DmarcAggregateReport
from app.models.dmarc_forensic import DmarcForensicReport
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.hosted_reports_poll_state import HostedReportsPollState
from app.models.job_run import JobRun
from app.models.mailbox_connection import MailboxConnection
from app.models.mfa_pending_challenge import MfaPendingChallenge
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.platform_admin import PlatformAdmin
from app.models.platform_admin_session import PlatformAdminSession
from app.models.session import UserSession
from app.models.source_ip_identity import SourceIpIdentity
from app.models.tls_rpt import TlsRptReport
from app.models.user import User
from app.models.user_recovery_code import UserRecoveryCode

__all__ = [
    "DkimSelector",
    "DismissedDetectedDomain",
    "DmarcAggregateRecord",
    "DmarcAggregateReport",
    "DmarcForensicReport",
    "DnsCheckResult",
    "Domain",
    "HostedReportsPollState",
    "JobRun",
    "MailboxConnection",
    "MfaPendingChallenge",
    "Organization",
    "PasswordSetupToken",
    "PlatformAdmin",
    "PlatformAdminSession",
    "UserSession",
    "SourceIpIdentity",
    "TlsRptReport",
    "User",
    "UserRecoveryCode",
]
