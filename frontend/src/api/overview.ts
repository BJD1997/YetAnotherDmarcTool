export interface CheckStatusCounts {
  pass: number;
  warn: number;
  fail: number;
  error: number;
}

export interface RankedDomain {
  domain_id: string;
  name: string;
  not_verified: boolean;
  insufficient_data: boolean;
  score: number | null;
  grade: string | null;
  message_volume: number;
  failed_volume: number;
  current_policy: string | null;
  last_report_at: string | null;
  check_status_counts: CheckStatusCounts;
  ready_to_enforce: boolean;
  dmarc_configured: boolean | null;
  rua_status: string | null;
  last_dns_check_at: string | null;
  mail_profile: "sends_mail" | "receive_only" | "parked";
}

/** Derives a human recommendation from the ranked-domain no-data signals
 * (only meaningful when insufficient_data is true) — "no report data yet"
 * on its own doesn't say whether that's fine, misconfigured, or unused. */
export function noDataRecommendation(ranked: RankedDomain): string {
  // A domain explicitly marked receive-only/parked is expected to have no
  // report data forever — that's the deliberate steady state, not a signal
  // something's misconfigured, so skip the dmarc_configured/rua_status
  // guessing entirely.
  if (ranked.mail_profile === "receive_only") return "Receive-only — no outbound mail expected";
  if (ranked.mail_profile === "parked") return "Not used for mail — no report data expected";
  if (ranked.dmarc_configured === false) return "DMARC not published — check DNS";
  if (ranked.last_dns_check_at === null) return "DNS checks haven't run yet";
  if (ranked.rua_status === "not_configured" || ranked.rua_status === "points_elsewhere") {
    return "rua= doesn't point here — check reporting setup";
  }
  if (ranked.rua_status === "lookup_error") return "Couldn't verify reporting DNS — check again later";
  return "Looks configured — likely just not sending mail yet";
}

export interface TrendPoint {
  date: string;
  total: number;
  dmarc_pass: number;
  spf_aligned: number;
  dkim_aligned: number;
  rejected: number;
}

export interface Posture {
  compliance_pct: number | null;
  current_policy: string | null;
  policy_distribution: Record<string, number> | null;
  failed_volume: number;
  report_freshness_hours: number | null;
  new_sender_count: number;
  ready_to_enforce_count: number;
}

export type ActionItemSeverity = "good" | "warning" | "serious" | "critical" | "neutral";

export interface ActionItem {
  severity: ActionItemSeverity;
  category: number;
  title: string;
  action_hint: string;
  domain_id: string | null;
}

export type SenderReviewStatus = "pending" | "approved" | "ignored" | "blocked";

export interface SenderSourceIp {
  source_ip: string;
  volume: number;
  spf_aligned_pct: number | null;
  dkim_aligned_pct: number | null;
  accepted: number;
  quarantined: number;
  rejected: number;
}

export interface SenderInventoryRow {
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
  likely_spoofed: boolean;
  source_ips: SenderSourceIp[];
  status: SenderReviewStatus;
  owner: string | null;
  notes: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export interface SenderReviewUpdate {
  service_label: string;
  status: SenderReviewStatus;
  owner: string | null;
  notes: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export const DATE_RANGE_PRESETS = [7, 30, 90] as const;
export type DateRangeDays = (typeof DATE_RANGE_PRESETS)[number];
