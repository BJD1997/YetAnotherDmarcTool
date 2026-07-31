export type CheckType = "spf" | "dkim" | "dmarc" | "dmarcbis" | "mta_sts" | "tls_rpt" | "dane" | "mx";
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
