import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { Domain } from "../../api/types";
import { makeTestQueryClient } from "../../test/render";
import DomainReports from "./DomainReports";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn() },
}));

const getMock = vi.mocked(api.get);

const DOMAIN: Domain = {
  id: "domain-1",
  name: "example.com",
  parent_domain_id: null,
  notes: null,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  verification_status: "verified",
  verified_at: "2026-01-01T00:00:00Z",
  verification_token: "token",
  verification_record_name: "_dmarc-verify.example.com",
  mail_profile: "sends_mail",
  hosted_report_address: null,
};

function renderReportsPage() {
  const queryClient = makeTestQueryClient();
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/domains/domain-1/reports"]}>
          <Routes>
            <Route path="/domains/:id" element={<Outlet context={DOMAIN} />}>
              <Route path="reports" element={<DomainReports />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => {
  getMock.mockReset();
  getMock.mockImplementation(async (url: string) => {
    if (url.includes("/reports/summary")) {
      return {
        total_reports: 0,
        total_messages: 0,
        accepted: 0,
        quarantined: 0,
        rejected: 0,
        dmarc_pass_pct: null,
        top_failing_source: null,
        last_report_received_at: null,
      };
    }
    if (url.includes("/reports/by-day")) {
      return { days: [], has_more: false, total: 0 };
    }
    throw new Error(`unexpected URL in test: ${url}`);
  });
});

describe("DomainReports filter form", () => {
  it("applies the reporter and source-ip filters together, instead of the second clobbering the first", async () => {
    renderReportsPage();

    fireEvent.change(screen.getByLabelText("Reporter"), {
      target: { value: "google.com" },
    });
    fireEvent.change(screen.getByLabelText("Source IP"), {
      target: { value: "203.0.113.5" },
    });
    fireEvent.click(screen.getByText("Apply"));

    await waitFor(() => {
      const calledWithBoth = getMock.mock.calls.some(
        ([url]) => url.includes("reporter=google.com") && url.includes("source_ip=203.0.113.5"),
      );
      expect(calledWithBoth).toBe(true);
    });
  });

  it("keeps a previously-applied reporter filter when only source-ip changes afterward", async () => {
    renderReportsPage();

    fireEvent.change(screen.getByLabelText("Reporter"), {
      target: { value: "google.com" },
    });
    fireEvent.click(screen.getByText("Apply"));
    await waitFor(() => expect(getMock.mock.calls.some(([url]) => url.includes("reporter=google.com"))).toBe(true));

    fireEvent.change(screen.getByLabelText("Source IP"), {
      target: { value: "203.0.113.5" },
    });
    fireEvent.click(screen.getByText("Apply"));

    await waitFor(() => {
      const calledWithBoth = getMock.mock.calls.some(
        ([url]) => url.includes("reporter=google.com") && url.includes("source_ip=203.0.113.5"),
      );
      expect(calledWithBoth).toBe(true);
    });
  });
});
