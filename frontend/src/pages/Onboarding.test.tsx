import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Onboarding, { deriveStep } from "./Onboarding";

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
  hostedMailboxReady = true,
  baselineDone = false,
  verified = baselineDone,
  unverifiedDomain = false,
  signInProvider = false,
  imapConnected = false,
  hostedAddress = null,
  newerPendingDomain = false,
}: {
  hostedMailboxReady?: boolean;
  baselineDone?: boolean;
  verified?: boolean;
  unverifiedDomain?: boolean;
  signInProvider?: boolean;
  imapConnected?: boolean;
  hostedAddress?: string | null;
  newerPendingDomain?: boolean;
} = {}) {
  getMock.mockImplementation(async (url: string) => {
    if (url === "/onboarding/status")
      return {
        org_name: "Local Org", user_role: "org_admin", has_mailbox: imapConnected || !!hostedAddress,
        mailbox_consent_granted: false, mailbox_last_sync_status: null, has_domain: verified || unverifiedDomain,
        has_verified_domain: verified, has_dns_baseline: baselineDone, has_any_report: false,
        hosted_mailbox_ready: hostedMailboxReady, has_sign_in_provider: signInProvider, wizard_completed: false,
      };
    if (url === "/organizations/current") return { name: "Local Org", entra_tenant_id: null, ask_ai_enabled: null };
    if (url === "/organizations/current/sign-in")
      return {
        provider: signInProvider ? "google" : "none", slug: "local-org", login_link: "https://dmarc.example/login/local-org",
        redirect_uri: "https://dmarc.example/api/auth/oidc/callback", entra_tenant_id: null,
        google_workspace_domain: signInProvider ? "example.com" : null, oidc: null,
        available: { microsoft: true, google: true, oidc: true }, can_change: true,
      };
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
      return verified || unverifiedDomain
        ? [{
            id: "d1", name: "example.com", parent_domain_id: null, created_at: "2026-10-01T00:00:00Z",
            verification_status: verified ? "verified" : "pending", verification_record_name: "_yadt.example.com",
            verification_token: "yadt-verify=abc123", hosted_report_address: hostedAddress,
          },
          ...(newerPendingDomain
            ? [{
                id: "d2", name: "newer.example", parent_domain_id: null, created_at: "2026-11-01T00:00:00Z",
                verification_status: "pending", verification_record_name: "_yadt.newer.example",
                verification_token: "yadt-verify=def456", hosted_report_address: null,
              }]
            : [])]
        : [];
    return null;
  });
}

beforeEach(() => {
  getMock.mockReset();
  vi.mocked(api.post).mockClear();
});

const base = {
  org_name: "Org", user_role: "org_admin" as const, has_mailbox: false, mailbox_consent_granted: false,
  mailbox_last_sync_status: null, has_domain: false, has_verified_domain: false, has_dns_baseline: false,
  has_any_report: false, hosted_mailbox_ready: true, has_sign_in_provider: false, wizard_completed: false,
};

describe("deriveStep", () => {
  it("starts at Welcome for an org that has done nothing", () => expect(deriveStep(base)).toBe(1));
  it("resumes at Domain while no domain is verified", () =>
    expect(deriveStep({ ...base, has_domain: true })).toBe(2));
  it("still starts at Welcome when only a sign-in provider was provisioned", () =>
    expect(deriveStep({ ...base, has_sign_in_provider: true })).toBe(1));
  it("goes to Sign-in after the domain is verified", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true })).toBe(3));
  it("goes to Mailbox once a provider is set", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true, has_sign_in_provider: true })).toBe(4));
  it("treats sign-in as skipped once a report destination exists (Settings done first)", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true, has_mailbox: true })).toBe(5));
  it("goes to Reporting after the baseline", () =>
    expect(deriveStep({ ...base, has_domain: true, has_verified_domain: true, has_mailbox: true, has_dns_baseline: true })).toBe(6));
});

describe("Step chips", () => {
  it("marks the steps before the resumed one as done", async () => {
    server({ verified: true, hostedAddress: "reports+abc@hosted.example" });
    renderWithAppProviders(<Onboarding />);

    expect(await screen.findByRole("button", { name: /Run baseline check/ })).toBeInTheDocument();
    for (const n of [1, 2, 3, 4]) expect(screen.getByTestId(`step-chip-${n}`)).toHaveTextContent("(done)");
    for (const n of [5, 6, 7, 8]) expect(screen.getByTestId(`step-chip-${n}`)).not.toHaveTextContent("(done)");
  });
});

describe("Domain step", () => {
  it("shows the verification record with copy buttons and waits for verification", async () => {
    server({ unverifiedDomain: true });
    renderWithAppProviders(<Onboarding />);

    expect(await screen.findByRole("heading", { name: /Verify you own example.com/ })).toBeInTheDocument();
    expect(screen.getByText("_yadt.example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy Host/name" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy Value" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Continue/ })).not.toBeInTheDocument();
  });
});

describe("Sign-in step", () => {
  it("can be skipped with local accounts only", async () => {
    server({ verified: true });
    renderWithAppProviders(<Onboarding />);
    fireEvent.click(await screen.findByRole("button", { name: /Only local accounts for now/ }));
    expect(await screen.findByRole("button", { name: /Use my own mailbox/ })).toBeInTheDocument();
  });
});

describe("Report mailbox step", () => {
  it("creates nothing until a choice is made, and offers both choices", async () => {
    server({ verified: true, signInProvider: true });
    renderWithAppProviders(<Onboarding />);

    expect(await screen.findByRole("button", { name: /Use my own mailbox/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Use a YetAnotherDmarcTool-hosted address/ })).toBeInTheDocument();
    expect(vi.mocked(api.post)).not.toHaveBeenCalledWith("/domains/d1/hosted-report-address");
    expect(screen.queryByRole("button", { name: /Continue/ })).not.toBeInTheDocument();
  });

  it("creates the hosted address only when clicked", async () => {
    server({ verified: true, signInProvider: true });
    vi.mocked(api.post).mockResolvedValueOnce({ hosted_report_address: "reports+abc@hosted.example" });
    renderWithAppProviders(<Onboarding />);

    fireEvent.click(await screen.findByRole("button", { name: /Use a YetAnotherDmarcTool-hosted address/ }));
    await waitFor(() => expect(vi.mocked(api.post)).toHaveBeenCalledWith("/domains/d1/hosted-report-address"));
    expect(await screen.findByText("reports+abc@hosted.example")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Continue/ })).toBeInTheDocument();
  });

  it("focuses the verified domain, not a newer pending one, and a hosted address unlocks Continue", async () => {
    server({ verified: true, signInProvider: true, hostedAddress: "reports+abc@hosted.example", newerPendingDomain: true });
    renderWithAppProviders(<Onboarding />);
    // has_mailbox is set, so the wizard resumes at the baseline; go back one step.
    fireEvent.click(await screen.findByRole("button", { name: /Back/ }));
    expect(await screen.findByText("reports+abc@hosted.example")).toBeInTheDocument();
    expect(screen.getByText(/Hosted address for example\.com/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Continue/ })).toBeInTheDocument();
  });

  it("unlocks Continue when the org has its own mailbox", async () => {
    server({ verified: true, signInProvider: true, imapConnected: true });
    renderWithAppProviders(<Onboarding />);
    fireEvent.click(await screen.findByRole("button", { name: /Back/ }));
    expect(await screen.findByRole("button", { name: /Continue/ })).toBeInTheDocument();
  });

  it("hides the hosted choice when the server has none", async () => {
    server({ verified: true, signInProvider: true, hostedMailboxReady: false });
    renderWithAppProviders(<Onboarding />);
    expect(await screen.findByRole("button", { name: /Use my own mailbox/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /hosted address/ })).not.toBeInTheDocument();
  });
});

describe("Onboarding after leaving it halfway", () => {
  it("resumes after the DNS baseline and is finished on reaching the waiting room", async () => {
    server({ baselineDone: true, hostedAddress: "reports+abc@hosted.example" });
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
    server({ verified: true, hostedAddress: "reports+abc@hosted.example" });
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
