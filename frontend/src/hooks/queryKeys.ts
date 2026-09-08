/** Canonical cache-key builders. Resource hooks and invalidations must use
 * these rather than repeating string arrays in individual pages. */
export const queryKeys = {
  domains: {
    all: ["domains"] as const,
    ranked: (days: number) => ["domains", "ranked", days] as const,
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
    organizations: ["admin-organizations"] as const,
    updates: ["admin-updates"] as const,
  },
} as const;
