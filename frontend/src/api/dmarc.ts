export interface DmarcSummary {
  total_message_count: number;
  dmarc_pass_count: number;
  dmarc_fail_count: number;
  by_disposition: Record<string, number>;
  report_count: number;
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

export interface DmarcDayRow {
  record_id: string;
  org_name: string;
  source_ip: string;
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

export interface MailboxConnectionStatus {
  id: string;
  mailbox_address: string;
  consent_status: "pending" | "granted" | "revoked";
  consent_granted_at: string | null;
  last_sync_at: string | null;
  last_sync_status: "success" | "error" | null;
  last_sync_error: string | null;
}
