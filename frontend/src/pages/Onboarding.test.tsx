import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Onboarding from "./Onboarding";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn().mockResolvedValue({}), patch: vi.fn().mockResolvedValue({}) },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "org_admin" } }) }));
const getMock = vi.mocked(api.get);

function server({
  hostedMailboxReady,
  baselineDone = false,
  verified = baselineDone,
  imapConnected = false,
  unverifiedDomain = false,
}: { hostedMailboxReady: boolean; baselineDone?: boolean; verified?: boolean; imapConnected?: boolean; unverifiedDomain?: boolean }) {
  getMock.mockImplementation(async (url: string) => {
    if (url === "/onboarding/status")
      return {
        org_name: "Local Org", user_role: "org_admin", has_mailbox: true, mailbox_consent_granted: false,
        mailbox_last_sync_status: null, has_domain: verified, has_verified_domain: verified,
        has_dns_baseline: baselineDone, has_any_report: false, hosted_mailbox_ready: hostedMailboxReady,
        wizard_completed: false,
      };
    if (url === "/organizations/current") return { name: "Local Org", entra_tenant_id: null, ask_ai_enabled: null };
    if (url === "/mailbox-connection" && imapConnected)
      return {
        id: "m1", kind: "imap", mailbox_address: "dmarc@example.com", consent_status: "granted", consent_granted_at: null,
        last_sync_at: null, last_sync_status: null, last_sync_error: null,
        imap: {
          host: "imap.mail.example", port: 993, security: "ssl", username: "someone@mail.example", report_address: "dmarc@example.com",
          folder: "INBOX", processed_folder: "DMARC processed", authserv_id: "mx.mail.example", detected_authserv_id: null, password_set: true,
        },
      };
    if (url === "/mailbox-connection") throw new ApiError(404, "no mailbox connection configured for your organization yet");
    if (url === "/domains")
      return verified
        ? [{ id: "d1", name: "example.com", parent_domain_id: null, created_at: "2026-10-01T00:00:00Z", verification_status: "verified", hosted_report_address: null }]
        : unverifiedDomain
          ? [{ id: "d1", name: "example.com", parent_domain_id: null, created_at: "2026-10-01T00:00:00Z", verification_status: "pending", hosted_report_address: null }]
          : [];
    return null;
  });
}

beforeEach(() => getMock.mockReset());

describe("Onboarding without Microsoft sign-in", () => {
  it("asks for the organization's own mailbox when this server has no hosted one", async () => {
    server({ hostedMailboxReady: false });
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));

    expect(await screen.findByText(/Connect the mailbox your DMARC reports arrive at/)).toBeInTheDocument();
    expect(await screen.findByPlaceholderText("imap.example.com")).toBeInTheDocument();
    expect(screen.queryByText(/generate a dedicated YetAnotherDmarcTool-hosted reporting address/)).not.toBeInTheDocument();
  });

  it("promises the hosted address when the server has one", async () => {
    server({ hostedMailboxReady: true });
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));

    expect(await screen.findByText(/generate a dedicated YetAnotherDmarcTool-hosted reporting address/)).toBeInTheDocument();
    expect(screen.queryByText(/This server can't receive DMARC reports yet/)).not.toBeInTheDocument();
  });

  it("doesn't offer a hosted address once the organization's own mailbox is connected", async () => {
    server({ hostedMailboxReady: true, imapConnected: true, unverifiedDomain: true });
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Get started/ }));
    expect(await screen.findByRole("heading", { name: "Report mailbox" })).toBeInTheDocument();
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/mailbox-connection"));
    expect(screen.queryByText(/generate a dedicated YetAnotherDmarcTool-hosted reporting address/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: /Add your first domain/ })).toBeInTheDocument();
    expect(screen.queryByText(/hosted reporting address for example.com/)).not.toBeInTheDocument();
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


describe("Onboarding DNS baseline", () => {
  it("shows the results in the wizard, problems first, without a link out of it", async () => {
    server({ hostedMailboxReady: true, verified: true });
    vi.mocked(api.post).mockResolvedValueOnce([
      { id: "c1", check_type: "spf", subject: null, status: "pass", summary: "SPF record is valid", details: {}, rule_version: null, checked_at: "" },
      { id: "c2", check_type: "mta_sts", subject: null, status: "warn", summary: "No MTA-STS policy", details: {}, rule_version: null, checked_at: "" },
    ]);
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Run baseline check/ }));

    const items = await screen.findAllByRole("listitem");
    expect(items[0]).toHaveTextContent("MTA-STS");
    expect(items[1]).toHaveTextContent("SPF");
    expect(screen.queryByRole("link", { name: /full breakdown/ })).not.toBeInTheDocument();
  });
});
