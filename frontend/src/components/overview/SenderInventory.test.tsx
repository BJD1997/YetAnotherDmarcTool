import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { Domain } from "../../api/types";
import type { SenderInventoryRow } from "../../api/overview";
import { makeTestQueryClient } from "../../test/render";
import SenderInventory from "./SenderInventory";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn() },
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { role: "org_admin" } }),
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

function row(overrides: Partial<SenderInventoryRow> = {}): SenderInventoryRow {
  return {
    service_label: "some-sender",
    match_method: "ip_fallback",
    volume: 5,
    source_ip_count: 1,
    spf_aligned_pct: 100,
    dkim_aligned_pct: 100,
    dmarc_pass_pct: 100,
    accepted: 5,
    quarantined: 0,
    rejected: 0,
    likely_spoofed: false,
    fcrdns_status: "pass",
    fcrdns_status_v4: "pass",
    fcrdns_status_v6: null,
    sends_ipv6: false,
    source_ips: [],
    status: "pending",
    owner: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    reviewed_at: null,
    ...overrides,
  };
}

function renderInventory(initialPath: string) {
  const queryClient = makeTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <SenderInventory domainId={DOMAIN.id} domains={[DOMAIN]} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMock.mockReset();
});

describe("SenderInventory ?highlight=", () => {
  it("shows a highlighted sender even when the active filter would otherwise hide it", async () => {
    // "archived" senders are hidden under the default "all" filter — the
    // highlight override must bypass that, or an action-queue link straight
    // to this sender would land on a page that looks empty.
    getMock.mockResolvedValue({
      [DOMAIN.id]: [row({ service_label: "sneaky-sender", status: "archived" })],
    });

    renderInventory("/domains/domain-1/senders?highlight=sneaky-sender");

    await waitFor(() => expect(screen.getByText("sneaky-sender")).toBeInTheDocument());
  });

  it("fetches all-time data instead of the 90-day default when a highlight target is present", async () => {
    getMock.mockResolvedValue({ [DOMAIN.id]: [row({ service_label: "old-sender" })] });

    renderInventory("/domains/domain-1/senders?highlight=old-sender");

    await waitFor(() => expect(getMock).toHaveBeenCalled());
    const [url] = getMock.mock.calls[0];
    expect(url).not.toContain("days=");
  });

  it("renders every sender normally when no highlight param is present", async () => {
    getMock.mockResolvedValue({ [DOMAIN.id]: [row({ service_label: "normal-sender" })] });

    renderInventory("/domains/domain-1/senders");

    await waitFor(() => expect(screen.getByText("normal-sender")).toBeInTheDocument());
    const [url] = getMock.mock.calls[0];
    expect(url).toContain("days=90");
  });
});

describe("SenderInventory filter correctness", () => {
  it("excludes an already-approved sender from 'Likely spoofed', matching the badge's own condition", async () => {
    getMock.mockResolvedValue({
      [DOMAIN.id]: [
        row({ service_label: "reviewed-and-fine", status: "approved", likely_spoofed: true }),
        row({ service_label: "genuinely-spoofed", status: "pending", likely_spoofed: true }),
      ],
    });

    renderInventory("/domains/domain-1/senders");
    await waitFor(() => expect(screen.getByText("reviewed-and-fine")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Likely spoofed/ }));

    expect(screen.getByText("genuinely-spoofed")).toBeInTheDocument();
    expect(screen.queryByText("reviewed-and-fine")).not.toBeInTheDocument();
  });

  it("shows a per-filter count that matches what clicking it actually reveals", async () => {
    getMock.mockResolvedValue({
      [DOMAIN.id]: [
        row({ service_label: "failing-sender", dmarc_pass_pct: 20 }),
        row({ service_label: "passing-sender", dmarc_pass_pct: 90 }),
      ],
    });

    renderInventory("/domains/domain-1/senders");

    expect(await screen.findByRole("button", { name: "Failing (1)" })).toBeInTheDocument();
  });
});
