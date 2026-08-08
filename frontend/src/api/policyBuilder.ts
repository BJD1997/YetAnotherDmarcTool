export interface DmarcCurrentRecord {
  raw: string;
  tags: Record<string, string>;
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

export type DmarcPolicy = "none" | "quarantine" | "reject";

export interface PolicyRecommendation {
  policy: DmarcPolicy;
  // RFC 9989 (DMARCbis) — policy for subdomains that don't exist in DNS at
  // all, distinct from sp= (subdomains that exist but lack their own DMARC
  // record). null means "inherit sp=/p=", same default behavior as leaving
  // sp= unset.
  np: DmarcPolicy | null;
  reasoning: string;
  blocked: boolean;
  blocking_reason: string | null;
}

export interface PolicyBuilderData {
  current_record: DmarcCurrentRecord | null;
  current_record_lookup_error: boolean;
  rua_destination: RuaDestination;
  org_mailbox_address: string | null;
  hosted_report_address: string | null;
  policy_stability_days: number;
  recommendation: PolicyRecommendation;
}
