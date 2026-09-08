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
  },
  currentUser: ["me"] as const,
  health: ["health"] as const,
  actionQueue: (domainId: string | null) => ["action-queue", domainId] as const,
  detectedDomains: ["detected-domains"] as const,
  dnsChecks: (domainId: string) => ["dns-checks", domainId] as const,
  dkimSelectors: (domainId: string) => ["dkim-selectors", domainId] as const,
  detectedDkimSelectors: (domainId: string) => ["dkim-selectors-detected", domainId] as const,
  senderInventory: ["sender-inventory"] as const,
} as const;
