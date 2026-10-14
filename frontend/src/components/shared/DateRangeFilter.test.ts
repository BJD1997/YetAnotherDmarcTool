import { describe, expect, it } from "vitest";
import { setDateRangeParams } from "./DateRangeFilter";

describe("setDateRangeParams", () => {
  it("sends the start of From and the start of the day after To, in local time", () => {
    const params = new URLSearchParams();
    setDateRangeParams(params, { from: "2026-09-03", to: "2026-09-07" });

    expect(params.get("from")).toBe(new Date(2026, 8, 3).toISOString());
    expect(params.get("to")).toBe(new Date(2026, 8, 8).toISOString());
  });

  it("leaves out empty ends", () => {
    const params = new URLSearchParams();
    setDateRangeParams(params, { from: "", to: "2026-09-07" });

    expect(params.has("from")).toBe(false);
    expect(params.has("to")).toBe(true);
  });
});
