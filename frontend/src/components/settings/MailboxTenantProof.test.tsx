import { act, fireEvent, screen, waitFor } from "@testing-library/react";
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
    return { name: "Acme", entra_tenant_id: null, sign_in_provider: "oidc", microsoft_mailbox_available: true, entra_consent_urls: null };
  });
  vi.mocked(api.post).mockReset().mockResolvedValue({ start_url: "/api/auth/sign-in-test/start" });
  vi.mocked(api.put).mockReset().mockResolvedValue({});
  vi.spyOn(window, "open").mockReturnValue(null);
});

describe("Microsoft 365 mailbox without Microsoft sign-in", () => {
  it("needs a test sign-in, and saves with its ticket", async () => {
    renderWithAppProviders(<MailboxConnectionSection canManage />);
    fireEvent.click(await screen.findByLabelText("Microsoft 365 mailbox"));
    fireEvent.change(screen.getByPlaceholderText("dmarc-reports@yourdomain.com"), { target: { value: "dmarc@acme.example" } });
    expect(screen.getByRole("button", { name: /Save & start syncing/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Test sign-in with Microsoft" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/mailbox-connection/microsoft/test"));
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: window.location.origin,
          data: { type: "yadt-sign-in-test", ok: true, ticket: "t1", email: "jan@acme.example", tenant_id: "1111-2222" },
        }),
      );
    });
    expect(await screen.findByText(/tenant 1111-2222/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Save & start syncing/ }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith("/mailbox-connection", { mailbox_address: "dmarc@acme.example", ticket: "t1" }));
  });
});
