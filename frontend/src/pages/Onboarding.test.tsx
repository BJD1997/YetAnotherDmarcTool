import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Onboarding from "./Onboarding";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn().mockResolvedValue({}), patch: vi.fn().mockResolvedValue({}) },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "org_admin" } }) }));
const getMock = vi.mocked(api.get);

function server({ hostedMailboxReady, baselineDone = false }: { hostedMailboxReady: boolean; baselineDone?: boolean }) {
  getMock.mockImplementation(async (url: string) => {
    if (url === "/onboarding/status")
      return {
        org_name: "Local Org", user_role: "org_admin", has_mailbox: true, mailbox_consent_granted: false,
        mailbox_last_sync_status: null, has_domain: baselineDone, has_verified_domain: baselineDone,
        has_dns_baseline: baselineDone, has_any_report: false, hosted_mailbox_ready: hostedMailboxReady,
        wizard_completed: false,
      };
    if (url === "/organizations/current") return { name: "Local Org", entra_tenant_id: null, ask_ai_enabled: null };
    if (url === "/domains")
      return baselineDone
        ? [{ id: "d1", name: "example.com", parent_domain_id: null, created_at: "2026-10-01T00:00:00Z", verification_status: "verified", hosted_report_address: null }]
        : [];
    return null;
  });
}

beforeEach(() => getMock.mockReset());

describe("Onboarding without Microsoft sign-in", () => {
  it("says plainly when this server has no report mailbox", async () => {
    server({ hostedMailboxReady: false });
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));

    expect(await screen.findByText(/This server can't receive DMARC reports yet/)).toBeInTheDocument();
    expect(screen.queryByText(/generate a dedicated YetAnotherDmarcTool-hosted reporting address/)).not.toBeInTheDocument();
  });

  it("promises the hosted address when the server has one", async () => {
    server({ hostedMailboxReady: true });
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));

    expect(await screen.findByText(/generate a dedicated YetAnotherDmarcTool-hosted reporting address/)).toBeInTheDocument();
    expect(screen.queryByText(/This server can't receive DMARC reports yet/)).not.toBeInTheDocument();
  });
});


describe("Onboarding after leaving it halfway", () => {
  it("resumes after the DNS baseline and is finished on reaching the waiting room", async () => {
    server({ hostedMailboxReady: true, baselineDone: true });
    renderWithAppProviders(<Onboarding />);

    expect(await screen.findByRole("heading", { name: /Make sure reports reach this dashboard/ })).toBeInTheDocument();
    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith("/onboarding/complete");

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: /AI assistant/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { name: /Waiting for your first report/ })).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledWith("/onboarding/complete"));
  });
});
