import enum


class OrganizationStatus(str, enum.Enum):
    pending_setup = "pending_setup"
    active = "active"
    suspended = "suspended"


class DomainVerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"


class ConsentStatus(str, enum.Enum):
    pending = "pending"
    granted = "granted"
    revoked = "revoked"


class SyncStatus(str, enum.Enum):
    success = "success"
    error = "error"


class CheckType(str, enum.Enum):
    spf = "spf"
    dkim = "dkim"
    dmarc = "dmarc"
    dmarcbis = "dmarcbis"
    mta_sts = "mta_sts"
    tls_rpt = "tls_rpt"
    dane = "dane"
    mx = "mx"


class CheckStatus(str, enum.Enum):
    pass_ = "pass"
    warn = "warn"
    fail = "fail"
    error = "error"


class Disposition(str, enum.Enum):
    none = "none"
    quarantine = "quarantine"
    reject = "reject"


class AuthResult(str, enum.Enum):
    pass_ = "pass"
    fail = "fail"


class TlsRptPolicyType(str, enum.Enum):
    tlsa = "tlsa"
    sts = "sts"
    no_policy_found = "no-policy-found"


class UserRole(str, enum.Enum):
    org_admin = "org_admin"
    member = "member"


class UserStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class JobType(str, enum.Enum):
    mailbox_poll = "mailbox_poll"
    dns_check = "dns_check"


class JobStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failure = "failure"
