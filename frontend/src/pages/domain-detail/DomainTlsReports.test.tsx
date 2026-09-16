import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { Outlet, Route, Routes } from "react-router-dom";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import DomainTlsReports from "./DomainTlsReports";

vi.mock("../../api/client", () => ({ api: { get: vi.fn() } }));
const getMock = vi.mocked(api.get);
beforeEach(() => {
  // Note: must be a block body, not `() => getMock.mockReset()` — mockReset()
  // returns the mock itself, and Vitest treats a function returned from
  // beforeEach as an implicit afterEach cleanup, which would then invoke
  // this mock a second time with no arguments after the test runs.
  getMock.mockReset();
});

const domain = { id: "domain-1", name: "example.com" } as const;

function renderPage() {
  return renderWithAppProviders(
    <Routes>
      <Route element={<Outlet context={domain} />}>
        <Route path="/" element={<DomainTlsReports />} />
      </Route>
    </Routes>,
  );
}

describe("DomainTlsReports", () => {
  it("shows a Load more button when the first page has more, and fetches the next page on click", async () => {
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("/summary")) return { total_reports: 3, total_successful_sessions: 30, total_failed_sessions: 0, failure_rate_pct: 0, distinct_reporting_orgs: 1, last_report_received_at: null, policy_type: null };
      if (url.includes("before_id=r1")) return { reports: [{ id: "r2", org_name: "sender.com", policy_type: "tlsa", date_range_begin: "2026-01-01T00:00:00Z", date_range_end: "2026-01-02T00:00:00Z", successful_session_count: 10, failed_session_count: 0, failure_details: [] }], has_more: false };
      return { reports: [{ id: "r1", org_name: "sender.com", policy_type: "tlsa", date_range_begin: "2026-01-02T00:00:00Z", date_range_end: "2026-01-03T00:00:00Z", successful_session_count: 20, failed_session_count: 0, failure_details: [] }], has_more: true };
    });

    renderPage();

    await waitFor(() => expect(screen.getByText("Load more")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Load more"));
    await waitFor(() => expect(screen.queryByText("Load more")).not.toBeInTheDocument());
  });
});
