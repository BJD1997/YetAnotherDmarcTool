import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import AdminJobRuns from "./AdminJobRuns";

vi.mock("../../api/client", () => ({ api: { get: vi.fn() } }));
const getMock = vi.mocked(api.get);
beforeEach(() => {
  // Note: must be a block body, not `() => getMock.mockReset()` — mockReset()
  // returns the mock itself, and Vitest treats a function returned from
  // beforeEach as an implicit afterEach cleanup, which would then invoke
  // this mock a second time with no arguments after the test runs.
  getMock.mockReset();
});

describe("AdminJobRuns", () => {
  it("has no Show-last dropdown and shows Load more when has_more is true", async () => {
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("job-runs/summary")) return { last_failure: null, success_rate_pct_24h: null, latest_mailbox_poll_at: null, reports_processed_today: 0 };
      if (url.includes("/admin/organizations/names")) return [];
      return {
        job_runs: [{ id: "run-1", job_type: "mailbox_poll", organization_id: null, domain_id: null, status: "success", started_at: "2026-01-01T00:00:00Z", finished_at: null, error_message: null, stats: null }],
        has_more: true,
      };
    });

    renderWithAppProviders(<AdminJobRuns />);

    // "mailbox_poll" alone is ambiguous while the job-runs query is still
    // loading -- the same string is also a static option in the job-type
    // filter -- so wait on "Load more", which only renders once the
    // job-runs page has actually loaded with has_more: true.
    await waitFor(() => expect(screen.getByText("Load more")).toBeInTheDocument());
    expect(screen.queryByText("Show last")).not.toBeInTheDocument();
    expect(screen.getAllByText("mailbox_poll").length).toBeGreaterThan(0);
  });

  it("shows more than 50 organizations in the filter dropdown (regression: was capped at the first paginated page)", async () => {
    const orgNames = Array.from({ length: 75 }, (_, i) => ({ id: `org-${i}`, name: `Org ${i}` }));
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("job-runs/summary")) return { last_failure: null, success_rate_pct_24h: null, latest_mailbox_poll_at: null, reports_processed_today: 0 };
      if (url.includes("/admin/organizations/names")) return orgNames;
      return { job_runs: [], has_more: false };
    });

    renderWithAppProviders(<AdminJobRuns />);

    await waitFor(() => expect(screen.getByText("Org 74")).toBeInTheDocument());
    const orgOptions = screen.getAllByRole("option").filter((o) => o.textContent?.startsWith("Org "));
    expect(orgOptions.length).toBe(75);
  });
});
