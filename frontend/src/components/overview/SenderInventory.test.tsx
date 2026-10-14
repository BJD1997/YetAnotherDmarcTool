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

  it("keeps the 90-day window when the highlighted sender is in it", async () => {
    getMock.mockResolvedValue({ [DOMAIN.id]: [row({ service_label: "recent-sender" })] });

    renderInventory("/domains/domain-1/senders?highlight=recent-sender");

    await waitFor(() => expect(screen.getByText("recent-sender")).toBeInTheDocument());
    const inventoryCalls = getMock.mock.calls.filter(([url]) => String(url).includes("sender-inventory"));
    expect(inventoryCalls.length).toBeGreaterThan(0);
    expect(inventoryCalls.every(([url]) => String(url).includes("days=90"))).toBe(true);
  });

  it("widens to all time when the highlighted sender isn't in the 90-day window", async () => {
    getMock.mockImplementation(async (url: string) =>
      url.includes("days=") ? { [DOMAIN.id]: [] } : { [DOMAIN.id]: [row({ service_label: "old-sender" })] },
    );

    renderInventory("/domains/domain-1/senders?highlight=old-sender");

    await waitFor(() => expect(screen.getByText("old-sender")).toBeInTheDocument());
    expect(getMock.mock.calls.some(([url]) => !String(url).includes("days="))).toBe(true);
  });

  it("renders every sender normally when no highlight param is present", async () => {
    getMock.mockResolvedValue({ [DOMAIN.id]: [row({ service_label: "normal-sender" })] });

    renderInventory("/domains/domain-1/senders");

    await waitFor(() => expect(screen.getByText("normal-sender")).toBeInTheDocument());
    const [url] = getMock.mock.calls.find(([u]) => String(u).includes("sender-inventory"))!;
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

describe("SenderInventory window", () => {
  it("defaults to the organization's rating period", async () => {
    getMock.mockImplementation(async (url: string) =>
      url.startsWith("/organizations/current") ? { rating_window_days: 30 } : { [DOMAIN.id]: [row({ service_label: "s" })] },
    );

    renderInventory("/domains/domain-1/senders");

    await waitFor(() =>
      expect(getMock.mock.calls.some(([url]) => String(url).includes("days=30"))).toBe(true),
    );
  });
});

describe("SenderInventory Ask AI", () => {
  function mockApi(rows: SenderInventoryRow[]) {
    getMock.mockImplementation(async (url: string) => {
      if (url === "/organizations/current") return { ask_ai_enabled: true, rating_window_days: 90 };
      if (url.startsWith("/ask-ai/prompt")) return { prompt: "the prompt" };
      return { [DOMAIN.id]: rows };
    });
  }

  it("isn't offered for blocked senders", async () => {
    mockApi([
      row({ service_label: "failing-sender", dmarc_pass_pct: 10, volume: 500 }),
      row({ service_label: "blocked-sender", dmarc_pass_pct: 0, volume: 500, status: "blocked" }),
    ]);

    renderInventory("/domains/domain-1/senders");

    await waitFor(() => expect(screen.getByText("blocked-sender")).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Ask AI/ })).toHaveLength(1));
  });

  it("asks about the period picked in the list", async () => {
    mockApi([row({ service_label: "failing-sender", dmarc_pass_pct: 10, volume: 500 })]);

    renderInventory("/domains/domain-1/senders");
    fireEvent.change(await screen.findByDisplayValue("Last 90 days"), { target: { value: "all" } });
    fireEvent.click(await screen.findByRole("button", { name: /Ask AI/ }));

    await waitFor(() =>
      expect(getMock.mock.calls.some(([url]) => String(url).startsWith("/ask-ai/prompt") && String(url).includes("period=all"))).toBe(true),
    );
  });
});

describe("SenderInventory period change", () => {
  it("keeps the current list on screen while another period loads", async () => {
    let releaseAllTime: (rows: unknown) => void = () => {};
    getMock.mockImplementation(async (url: string) => {
      if (url === "/organizations/current") return { ask_ai_enabled: false, rating_window_days: 90 };
      if (url.includes("days=90")) return { [DOMAIN.id]: [row({ service_label: "recent-sender" })] };
      return new Promise((resolve) => { releaseAllTime = resolve; });
    });

    renderInventory("/domains/domain-1/senders");
    fireEvent.change(await screen.findByDisplayValue("Last 90 days"), { target: { value: "all" } });

    expect(await screen.findByText("Updating…")).toBeInTheDocument();
    expect(screen.getByText("recent-sender")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();

    releaseAllTime({ [DOMAIN.id]: [row({ service_label: "old-sender" })] });
    expect(await screen.findByText("old-sender")).toBeInTheDocument();
    expect(screen.queryByText("Updating…")).not.toBeInTheDocument();
  });
});
