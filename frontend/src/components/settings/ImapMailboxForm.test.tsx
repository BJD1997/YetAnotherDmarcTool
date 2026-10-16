import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import ImapMailboxForm from "./ImapMailboxForm";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
const postMock = vi.mocked(api.post);
const putMock = vi.mocked(api.put);

beforeEach(() => {
  postMock.mockReset();
  putMock.mockReset().mockResolvedValue({});
});

describe("ImapMailboxForm", () => {
  it("fills in the mail server's ID from the connection test", async () => {
    postMock.mockResolvedValueOnce({ ok: true, messages: 12, detected_authserv_id: "mx.mail.example" });
    renderWithAppProviders(<ImapMailboxForm onSaved={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText("imap.example.com"), { target: { value: "imap.mail.example" } });
    fireEvent.change(screen.getByPlaceholderText("dmarc-reports@example.com"), { target: { value: "reports@mail.example" } });
    fireEvent.change(screen.getByPlaceholderText("app password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText(/Connected: 12 messages in INBOX/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("mx.mail.example")).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/mailbox-connection/imap/test", expect.objectContaining({ port: 993, security: "ssl" }));
  });

  it("switches the port with the security choice, and keeps the stored password when left empty", async () => {
    renderWithAppProviders(
      <ImapMailboxForm
        onSaved={vi.fn()}
        current={{
          host: "imap.mail.example", port: 993, security: "ssl", username: "reports@mail.example", report_address: "reports@mail.example",
          folder: "INBOX", processed_folder: "DMARC processed", authserv_id: "mx.mail.example", detected_authserv_id: null, password_set: true,
        }}
      />,
    );

    fireEvent.change(screen.getByDisplayValue("SSL/TLS"), { target: { value: "starttls" } });
    expect(screen.getByDisplayValue("143")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save & start syncing" }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    const body = putMock.mock.calls[0][1] as Record<string, unknown>;
    expect(body).not.toHaveProperty("password");
    expect(body).not.toHaveProperty("report_address");
    expect(body).toMatchObject({ port: 143, security: "starttls", authserv_id: "mx.mail.example" });
  });

  it("sends a report address that differs from the username, and shows the stored one", async () => {
    renderWithAppProviders(
      <ImapMailboxForm
        onSaved={vi.fn()}
        current={{
          host: "imap.mail.me.com", port: 993, security: "ssl", username: "someone@icloud.com", report_address: "dmarc@example.com",
          folder: "INBOX", processed_folder: "DMARC processed", authserv_id: null, detected_authserv_id: null, password_set: true,
        }}
      />,
    );

    expect(screen.getByDisplayValue("dmarc@example.com")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save & start syncing" }));

    await waitFor(() => expect(putMock).toHaveBeenCalled());
    expect(putMock.mock.calls[0][1]).toMatchObject({ username: "someone@icloud.com", report_address: "dmarc@example.com" });
  });
});
