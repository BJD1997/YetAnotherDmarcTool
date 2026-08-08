export interface DmarcSummary {
  total_message_count: number;
  dmarc_pass_count: number;
  dmarc_fail_count: number;
  by_disposition: Record<string, number>;
  report_count: number;
  current_policy: string | null;
}

export interface DmarcOutboundService {
  service_label: string;
  match_method: "pattern" | "ptr_domain" | "ip_fallback";
  volume: number;
  source_ip_count: number;
  spf_aligned_pct: number | null;
  dkim_aligned_pct: number | null;
  dmarc_pass_pct: number | null;
  accepted: number;
  quarantined: number;
  rejected: number;
}

export interface InboundHostRow {
  host: string;
  priority: number | null;
  provider_label: string;
  mx_status: "pass" | "warn" | "fail" | "error" | null;
  starttls_status: "pass" | "warn" | "fail" | "error" | null;
  dane_status: "pass" | "warn" | "fail" | "error" | null;
  mta_sts_status: "pass" | "not_covered" | "not_configured";
}

export type TlsRptPolicyType = "tlsa" | "sts" | "no-policy-found";

// RFC 8460 §4.3's fixed result-type vocabulary — used as the filter
// dropdown's options, same convention as the DMARC Reports tab's fixed
// disposition/spf_result/dkim_result selects.
export const TLS_RPT_RESULT_TYPES = [
  "starttls-not-supported",
  "certificate-host-mismatch",
  "certificate-expired",
  "certificate-not-trusted",
  "validation-failure",
  "sts-policy-invalid",
  "sts-policy-fetch-error",
  "sts-webpki-invalid",
  "dane-required",
  "tlsa-invalid",
  "dnssec-invalid",
  "dane-required-no-tlsa-record",
] as const;

export interface TlsRptFailureDetail {
  result_type: string;
  receiving_ip: string | null;
  sending_mta_ip: string | null;
  receiving_mx_hostname: string | null;
  failed_session_count: number;
}

export interface TlsRptFailureReason {
  result_type: string;
  count: number;
}

export interface TlsRptSenderSummary {
  org_name: string;
  successful_session_count: number;
  failed_session_count: number;
  failure_reasons: TlsRptFailureReason[];
}

export interface TlsRptReportRow {
  id: string;
  org_name: string;
  policy_type: TlsRptPolicyType;
  date_range_begin: string;
  date_range_end: string;
  successful_session_count: number;
  failed_session_count: number;
  failure_details: TlsRptFailureDetail[];
}

export interface TlsRptSummary {
  total_reports: number;
  total_successful_sessions: number;
  total_failed_sessions: number;
  failure_rate_pct: number | null;
  distinct_reporting_orgs: number;
  last_report_received_at: string | null;
  policy_type: TlsRptPolicyType | null;
}

export interface TlsRptFilters {
  days?: number;
  org_name?: string;
  result_type?: string;
  failures_only?: boolean;
}

export function tlsRptFilterQuery(filters: TlsRptFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "" && value !== false) params.set(key, String(value));
  }
  return params.toString();
}

export interface DmarcDayRow {
  record_id: string;
  org_name: string;
  source_ip: string;
  service_label: string;
  count: number;
  disposition: "none" | "quarantine" | "reject";
  spf_result: "pass" | "fail";
  dkim_result: "pass" | "fail";
}

export interface DmarcDayGroup {
  date: string;
  report_count: number;
  message_count: number;
  accepted: number;
  quarantined: number;
  rejected: number;
  rows: DmarcDayRow[];
}

export interface DmarcReportsByDay {
  days: DmarcDayGroup[];
  has_more: boolean;
}

export interface ReportsFilters {
  days?: number;
  disposition?: "none" | "quarantine" | "reject";
  spf_result?: "pass" | "fail";
  dkim_result?: "pass" | "fail";
  reporter?: string;
  source_ip?: string;
}

export function reportsFilterQuery(filters: ReportsFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export interface DmarcReportsSummary {
  total_reports: number;
  total_messages: number;
  accepted: number;
  quarantined: number;
  rejected: number;
  dmarc_pass_pct: number | null;
  top_failing_source: { source_ip: string; service_label: string; failed_count: number } | null;
  last_report_received_at: string | null;
}

export interface DmarcReportsGroupedRow {
  key: string;
  label: string;
  message_count: number;
  report_count: number;
  accepted: number;
  quarantined: number;
  rejected: number;
  dmarc_pass_pct: number | null;
}

export interface DmarcRecordDetail {
  id: string;
  report: {
    id: string;
    report_id: string;
    org_name: string;
    email: string | null;
    date_range_begin: string;
    date_range_end: string;
    policy_p: string | null;
    policy_sp: string | null;
    policy_pct: number | null;
    policy_adkim: string | null;
    policy_aspf: string | null;
  };
  source_ip: string;
  count: number;
  disposition: "none" | "quarantine" | "reject";
  spf_result: "pass" | "fail";
  dkim_result: "pass" | "fail";
  header_from: string;
  envelope_from: string | null;
  envelope_to: string | null;
  auth_results: Record<string, unknown>;
  spf_narrative: string[];
  dkim_narrative: string[];
  verdict: {
    spf_aligned: boolean;
    dkim_aligned: boolean;
    dmarc_aligned: boolean;
    disposition_applied: string;
  };
}

export interface RatingFactor {
  factor: string;
  weight: number;
  score_pct: number;
  detail: string;
}

export interface DomainRating {
  not_verified: boolean;
  insufficient_data: boolean;
  score: number | null;
  grade: string | null;
  factors: RatingFactor[];
}

export interface MailboxSyncStats {
  messages_seen: number;
  aggregate_reports: number;
  forensic_reports: number;
  tls_rpt_policies: number;
  errors: number;
}

export interface MailboxConnectionStatus {
  id: string;
  mailbox_address: string;
  consent_status: "pending" | "granted" | "revoked";
  consent_granted_at: string | null;
  last_sync_at: string | null;
  last_sync_status: "success" | "error" | null;
  last_sync_error: string | null;
  last_report_at: string | null;
  last_run_stats: MailboxSyncStats | null;
}

export interface MailboxJobRun {
  id: string;
  status: "running" | "success" | "failure";
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  stats: MailboxSyncStats | null;
}
