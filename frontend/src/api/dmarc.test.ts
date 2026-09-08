import { describe, expect, it } from "vitest";

import { reportsFilterQuery, tlsRptFilterQuery } from "./dmarc";

describe("report query serialization", () => {
  it("omits empty DMARC filters and retains meaningful values", () => {
    expect(
      reportsFilterQuery({
        days: 30,
        disposition: "reject",
        reporter: "",
        source_ip: undefined,
      }),
    ).toBe("days=30&disposition=reject");
  });

  it("omits false TLS-only filters", () => {
    expect(tlsRptFilterQuery({ days: 7, failures_only: false, result_type: "certificate-expired" })).toBe(
      "days=7&result_type=certificate-expired",
    );
  });
});
