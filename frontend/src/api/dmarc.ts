export interface DmarcSummary {
  total_message_count: number;
  dmarc_pass_count: number;
  dmarc_fail_count: number;
  by_disposition: Record<string, number>;
  report_count: number;
}

export interface DmarcSource {
  source_ip: string;
  header_from: string;
  count: number;
}

export interface DmarcReportListItem {
  id: string;
  org_name: string;
  report_id: string;
  date_range_begin: string;
  date_range_end: string;
  policy_p: string | null;
  policy_pct: number | null;
  received_at: string;
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
