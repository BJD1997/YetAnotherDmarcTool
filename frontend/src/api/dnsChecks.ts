export type CheckType = "spf" | "dkim" | "dmarc" | "dmarcbis" | "mta_sts" | "tls_rpt" | "dane" | "mx" | "starttls";
export type CheckStatus = "pass" | "warn" | "fail" | "error";

export interface CheckResult {
  id: string;
  check_type: CheckType;
  subject: string | null;
  status: CheckStatus;
  summary: string;
  details: Record<string, unknown>;
  rule_version: string | null;
  checked_at: string;
}

export interface DkimSelectorItem {
  id: string;
  domain_id: string;
  selector: string;
  description: string | null;
  created_at: string;
}

export interface DetectedSelector {
  selector: string;
  report_count: number;
  message_volume: number;
}

export type MtaStsMode = "none" | "testing" | "enforce";

export interface MtaStsCurrentPolicy {
  raw: string;
  mode: string | null;
  mx_patterns: string[];
  max_age: string | null;
}

export interface MtaStsBuilderData {
  mx_hosts: string[];
  current_txt: string | null;
  current_policy: MtaStsCurrentPolicy | null;
  current_policy_fetch_error: string | null;
  recommended_mode: MtaStsMode;
}

export type RuaDestinationStatus =
  | "not_configured"
  | "lookup_error"
  | "no_rua"
  | "points_elsewhere"
  | "correct"
  | "no_mailbox";

export interface RuaDestination {
  status: RuaDestinationStatus;
  current_targets: string[];
}

export interface TlsRptCurrentRecord {
  raw: string;
  tags: Record<string, string>;
}

export interface TlsRptBuilderData {
  current_record: TlsRptCurrentRecord | null;
  current_record_lookup_error: boolean;
  rua_destination: RuaDestination;
  org_mailbox_address: string | null;
  hosted_report_address: string | null;
}
