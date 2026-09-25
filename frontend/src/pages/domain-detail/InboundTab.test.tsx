import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { InboundHostRow } from "../../api/dmarc";
import type { Domain } from "../../api/types";
import { makeTestQueryClient } from "../../test/render";
import InboundTab from "./InboundTab";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn() },
}));

const getMock = vi.mocked(api.get);

function domain(overrides: Partial<Domain> = {}): Domain {
  return {
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
    ...overrides,
  };
}

function renderInboundTab(d: Domain) {
  const queryClient = makeTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/domains/domain-1/inbound"]}>
        <Routes>
          <Route path="/domains/:domainId" element={<Outlet context={d} />}>
            <Route path="inbound" element={<InboundTab />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
});

describe("InboundTab", () => {
  it("does not show the MX host table for a domain marked 'not used for mail'", () => {
    renderInboundTab(domain({ mail_profile: "parked" }));

    expect(screen.getByText(/not used for mail/i)).toBeInTheDocument();
    expect(getMock).not.toHaveBeenCalled();
  });

  it("links to DNS checks instead of claiming a Recheck button that isn't on this page", async () => {
    getMock.mockResolvedValue([] satisfies InboundHostRow[]);

    renderInboundTab(domain());

    const link = await screen.findByRole("link", { name: "DNS checks" });
    expect(link).toHaveAttribute("href", "/domains/domain-1/dns");
    expect(screen.queryByText(/Recheck now/)).not.toBeInTheDocument();
  });

  it("doesn't claim no hosts were checked while the hosts are still loading", async () => {
    getMock.mockReturnValue(new Promise(() => {}));

    renderInboundTab(domain());

    expect(await screen.findByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText(/No MX hosts checked yet/)).not.toBeInTheDocument();
  });

  it("renders the host table once hosts are present", async () => {
    getMock.mockResolvedValue([
      {
        host: "mx1.example.com",
        priority: 10,
        provider_label: "Google",
        mx_status: "pass",
        starttls_status: "pass",
        dane_status: null,
        mta_sts_status: "not_configured",
      },
    ] satisfies InboundHostRow[]);

    renderInboundTab(domain());

    await waitFor(() => expect(screen.getByText("mx1.example.com")).toBeInTheDocument());
  });
});
