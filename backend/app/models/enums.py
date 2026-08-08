import enum


class OrganizationStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"


class DomainVerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"


# receive_only and parked get identical DMARC-lockdown treatment today (no
# legitimate outbound mail to protect, so recommend p=reject/np=reject
# immediately instead of waiting on report volume that will never arrive)
# but are stored as distinct values in case their treatment ever needs to
# diverge later (e.g. inbound-check emphasis for receive_only), rather than
# collapsing them into one boolean now and needing another migration then.
class DomainMailProfile(str, enum.Enum):
    sends_mail = "sends_mail"
    receive_only = "receive_only"
    parked = "parked"


class ConsentStatus(str, enum.Enum):
    pending = "pending"
    granted = "granted"
    revoked = "revoked"


# strict: -all (hardfail) always scored pass, ~all (softfail) always warn —
# the traditional advice. conditional: for a sends_mail domain whose own
# DMARC policy is already quarantine/reject, ~all scores pass instead —
# DMARC is already the enforcement mechanism at that point, so -all only
# adds a deliverability risk (SMTP-level bounce on relayed mail before
# DKIM/DMARC evaluation) with no security benefit. See spf.py's check().
class SpfAllQualifierMode(str, enum.Enum):
    strict = "strict"
    conditional = "conditional"


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
    starttls = "starttls"


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


class AuthMethod(str, enum.Enum):
    entra = "entra"
    local = "local"


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


class SourceMatchMethod(str, enum.Enum):
    pattern = "pattern"
    ptr_domain = "ptr_domain"
    ip_fallback = "ip_fallback"


class SenderReviewStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    ignored = "ignored"
    blocked = "blocked"
