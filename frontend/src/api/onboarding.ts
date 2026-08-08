export interface OnboardingStatus {
  org_name: string | null;
  user_role: "org_admin" | "member";
  has_mailbox: boolean;
  mailbox_consent_granted: boolean;
  mailbox_last_sync_status: "success" | "error" | null;
  has_domain: boolean;
  has_verified_domain: boolean;
  has_dns_baseline: boolean;
  has_any_report: boolean;
}
