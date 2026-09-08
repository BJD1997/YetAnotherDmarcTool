/** Canonical cache-key builders. Resource hooks and invalidations must use
 * these rather than repeating string arrays in individual pages. */
export const queryKeys = {
  domains: {
    all: ["domains"] as const,
    ranked: ["domains-ranked"] as const,
    detail: (domainId: string) => ["domains", domainId] as const,
  },
  organization: {
    current: ["organization", "current"] as const,
  },
  mailboxConnection: {
    current: ["mailbox-connection"] as const,
    jobRuns: ["mailbox-job-runs"] as const,
  },
  onboarding: {
    status: ["onboarding-status"] as const,
  },
  users: {
    all: ["users"] as const,
  },
  admin: {
    currentUser: ["admin-me"] as const,
    organizations: ["admin-organizations"] as const,
    updates: ["admin-updates"] as const,
    jobRunsSummary: ["admin-job-runs-summary"] as const,
    jobRuns: (filters: string) => ["admin-job-runs", filters] as const,
  },
  currentUser: ["me"] as const,
  health: ["health"] as const,
  actionQueue: (domainId: string | null) => ["action-queue", domainId] as const,
  detectedDomains: ["detected-domains"] as const,
  dnsChecks: (domainId: string) => ["dns-checks", domainId] as const,
  dkimSelectors: (domainId: string) => ["dkim-selectors", domainId] as const,
  detectedDkimSelectors: (domainId: string) => ["dkim-selectors-detected", domainId] as const,
  senderInventory: {
    all: ["sender-inventory"] as const,
    list: (domainIds: string, days: number | null) => ["sender-inventory", domainIds, days] as const,
  },
  trend: (domainId: string | null, days: number) => ["dmarc-trend", domainId, days] as const,
  posture: (domainId: string | null, days: number) => ["dmarc-posture", domainId, days] as const,
  dmarcSummary: (domainId: string) => ["dmarc-summary", domainId] as const,
  domainRating: (domainId: string) => ["domain-rating", domainId] as const,
  inboundHosts: (domainId: string) => ["dmarc-inbound", domainId] as const,
  ruaCheck: (domainId: string) => ["rua-check", domainId] as const,
  dmarcReports: {
    summary: (domainId: string, filters: string) => ["dmarc-reports-summary", domainId, filters] as const,
    byDay: (domainId: string, filters: string) => ["dmarc-reports-by-day", domainId, filters] as const,
    grouped: (domainId: string, grouping: string, filters: string) => ["dmarc-reports-grouped", domainId, grouping, filters] as const,
    detail: (domainId: string, recordId: string | null) => ["dmarc-record-detail", domainId, recordId] as const,
  },
  tlsReports: {
    summary: (domainId: string, filters: string) => ["tls-rpt-summary", domainId, filters] as const,
    reports: (domainId: string, filters: string) => ["tls-rpt-reports", domainId, filters] as const,
    bySender: (domainId: string, filters: string) => ["tls-rpt-by-sender", domainId, filters] as const,
  },
  signInEvents: (filters: string) => ["sign-in-events", filters] as const,
  policyBuilder: (domainId: string) => ["policy-builder", domainId] as const,
  tlsRptBuilder: (domainId: string) => ["tls-rpt-builder", domainId] as const,
  mtaStsBuilder: (domainId: string) => ["mta-sts-builder", domainId] as const,
} as const;
