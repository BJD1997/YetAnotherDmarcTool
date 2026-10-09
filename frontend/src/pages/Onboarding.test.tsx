import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Onboarding from "./Onboarding";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "org_admin" } }) }));
const getMock = vi.mocked(api.get);

function server({ hostedMailboxReady }: { hostedMailboxReady: boolean }) {
  getMock.mockImplementation(async (url: string) => {
    if (url === "/onboarding/status")
      return {
        org_name: "Local Org", user_role: "org_admin", has_mailbox: true, mailbox_consent_granted: false,
        mailbox_last_sync_status: null, has_domain: false, has_verified_domain: false, has_dns_baseline: false,
        has_any_report: false, hosted_mailbox_ready: hostedMailboxReady,
      };
    if (url === "/organizations/current") return { name: "Local Org", entra_tenant_id: null, ask_ai_enabled: null };
    if (url === "/domains") return [];
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
