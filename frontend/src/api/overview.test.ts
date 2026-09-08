import { describe, expect, it } from "vitest";

import { noDataRecommendation, type RankedDomain } from "./overview";

function domain(overrides: Partial<RankedDomain> = {}): RankedDomain {
  return {
    domain_id: "domain-1",
    name: "example.com",
    not_verified: false,
    insufficient_data: true,
    score: null,
    grade: null,
    message_volume: 0,
    failed_volume: 0,
    current_policy: null,
    last_report_at: null,
    check_status_counts: { pass: 0, warn: 0, fail: 0, error: 0 },
    ready_to_enforce: false,
    dmarc_configured: true,
    rua_status: "correct",
    last_dns_check_at: "2026-09-08T12:00:00Z",
    mail_profile: "sends_mail",
    ...overrides,
  };
}

describe("noDataRecommendation", () => {
  it.each([
    [domain({ mail_profile: "receive_only" }), "Receive-only — no outbound mail expected"],
    [domain({ mail_profile: "parked" }), "Not used for mail — no report data expected"],
    [domain({ dmarc_configured: false }), "DMARC not published — check DNS"],
    [domain({ last_dns_check_at: null }), "DNS checks haven't run yet"],
    [domain({ rua_status: "points_elsewhere" }), "rua= doesn't point here — check reporting setup"],
    [domain({ rua_status: "lookup_error" }), "Couldn't verify reporting DNS — check again later"],
    [domain(), "Looks configured — likely just not sending mail yet"],
  ])("returns the expected operational guidance", (ranked, expected) => {
    expect(noDataRecommendation(ranked)).toBe(expected);
  });
});
