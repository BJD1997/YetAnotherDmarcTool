import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import MailboxConnectionSection from "./MailboxConnectionSection";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

beforeEach(() => {
  vi.mocked(api.get).mockReset().mockImplementation(async (url: string) => {
    if (url === "/mailbox-connection") throw new ApiError(404, "no mailbox connection configured for your organization yet");
    if (url.startsWith("/mailbox-connection/job-runs")) return [];
    return {
      name: "Acme", entra_tenant_id: null, sign_in_provider: "google", microsoft_mailbox_available: false, entra_consent_urls: null,
      google_mailbox: { client_id: "1234567890", scope: "https://www.googleapis.com/auth/gmail.modify" },
    };
  });
  vi.mocked(api.post).mockReset().mockResolvedValue({ ok: true, messages: 3 });
  vi.mocked(api.put).mockReset().mockResolvedValue({});
});

describe("Google Workspace mailbox", () => {
  it("shows the delegation details, tests and saves", async () => {
    renderWithAppProviders(<MailboxConnectionSection canManage />);
    fireEvent.click(await screen.findByLabelText("Google Workspace mailbox"));
    expect(screen.getByText("1234567890")).toBeInTheDocument();
    expect(screen.getByText("https://www.googleapis.com/auth/gmail.modify")).toBeInTheDocument();
    expect(screen.queryByLabelText("Microsoft 365 mailbox")).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("dmarc-reports@yourdomain.com"), { target: { value: "dmarc@acme.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connected: 3 messages/)).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith("/mailbox-connection/google/test", { mailbox_address: "dmarc@acme.example" });

    fireEvent.click(screen.getByRole("button", { name: /Save & start syncing/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith("/mailbox-connection/google", { mailbox_address: "dmarc@acme.example" }));
  });
});
