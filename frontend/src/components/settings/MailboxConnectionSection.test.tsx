import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import MailboxConnectionSection from "./MailboxConnectionSection";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
const getMock = vi.mocked(api.get);
const postMock = vi.mocked(api.post);

const CONNECTION = {
  id: "c1",
  mailbox_address: "reports@example.com",
  consent_status: "granted",
  consent_granted_at: "2026-10-01T00:00:00Z",
  last_sync_at: "2026-10-02T08:00:00Z",
  last_sync_status: "success",
  last_sync_error: null,
  last_report_at: null,
  last_run_stats: null,
};

describe("MailboxConnectionSection resync", () => {
  let lastSync: string;

  beforeEach(() => {
    lastSync = CONNECTION.last_sync_at;
    getMock.mockReset();
    postMock.mockReset();
    getMock.mockImplementation(async (url: string) => {
      if (url === "/mailbox-connection") return { ...CONNECTION, last_sync_at: lastSync };
      if (url.startsWith("/mailbox-connection/job-runs")) return [];
      return { entra_consent_urls: null };
    });
  });

  it("says syncing until the worker's sync shows up, then refreshes the history", async () => {
    postMock.mockResolvedValue({ status: "resync started" });
    renderWithAppProviders(<MailboxConnectionSection canManage />);

    fireEvent.click(await screen.findByRole("button", { name: /Recent syncs/ }));
    const historyLoads = () => getMock.mock.calls.filter(([url]) => String(url).startsWith("/mailbox-connection/job-runs")).length;
    await waitFor(() => expect(historyLoads()).toBe(1));

    fireEvent.click(screen.getByRole("button", { name: /Resync now/ }));
    expect(await screen.findByRole("button", { name: /Syncing…/ })).toBeDisabled();

    // The worker finishes: the server now reports a newer last sync.
    lastSync = "2026-10-02T09:00:00Z";
    await waitFor(() => expect(screen.getByRole("button", { name: /Resync now/ })).toBeEnabled(), { timeout: 5000 });
    await waitFor(() => expect(historyLoads()).toBe(2));
  });
});
