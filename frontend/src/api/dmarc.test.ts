import { describe, expect, it } from "vitest";

import { reportsFilterQuery, tlsRptFilterQuery } from "./dmarc";

describe("report query serialization", () => {
  it("omits empty DMARC filters and retains meaningful values", () => {
    expect(
      reportsFilterQuery({
        date_from: "2026-08-01",
        date_to: "2026-08-30",
        disposition: "reject",
        reporter: "",
        source_ip: undefined,
      }),
    ).toBe("date_from=2026-08-01&date_to=2026-08-30&disposition=reject");
  });

  it("omits false TLS-only filters", () => {
    expect(
      tlsRptFilterQuery({ date_from: "2026-08-01", date_to: "2026-08-07", failures_only: false, result_type: "certificate-expired" }),
    ).toBe("date_from=2026-08-01&date_to=2026-08-07&result_type=certificate-expired");
  });
});
