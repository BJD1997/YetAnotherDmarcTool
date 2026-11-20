import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import LeftOutReportsNotice from "./LeftOutReportsNotice";

vi.mock("../../api/client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "org_admin" } }) }));

const REPORTS = [
  { id: "a", type: "DMARC", reporter: "google.com", period_start: "2026-09-20T00:00:00Z", received_at: "2026-09-21T08:00:00Z", sender_domain: "evil.example", reason: "Sent by evil.example, not by the reporter it names", trustable: false },
  { id: "b", type: "TLS-RPT", reporter: "Google Inc.", period_start: "2026-09-20T00:00:00Z", received_at: "2026-09-21T09:00:00Z", sender_domain: "google.com", reason: "Failed DMARC for google.com", trustable: false },
  { id: "c", type: "DMARC", reporter: "partner.example", period_start: "2026-09-20T00:00:00Z", received_at: "2026-09-21T10:00:00Z", sender_domain: "partner.example", reason: "Sent by partner.example, a reporter you haven't trusted yet", trustable: true },
];

describe("LeftOutReportsNotice", () => {
  it("counts this tab's left-out reports and lists them with the reason", async () => {
    vi.mocked(api.get).mockResolvedValueOnce(REPORTS);
    renderWithAppProviders(<LeftOutReportsNotice domainId="d1" types={["DMARC", "Forensic"]} />);

    expect(await screen.findByText(/2 reports were left out/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review" }));

    expect(screen.getByText("Sent by evil.example, not by the reporter it names")).toBeInTheDocument();
    expect(screen.queryByText("Failed DMARC for google.com")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "report sender check" })).toHaveAttribute("href", "/settings");
  });

  it("offers to trust only a reporter whose trust alone would count its report", async () => {
    vi.mocked(api.get).mockResolvedValueOnce(REPORTS);
    vi.mocked(api.post).mockResolvedValueOnce([{ domain: "partner.example", created_at: "2026-10-09T00:00:00Z" }]);
    renderWithAppProviders(<LeftOutReportsNotice domainId="d1" types={["DMARC"]} />);
    fireEvent.click(await screen.findByRole("button", { name: "Review" }));

    expect(screen.queryByRole("button", { name: "Trust evil.example" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Trust partner.example" }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/organizations/current/trusted-reporters", { domain: "partner.example" }),
    );
  });

  it("shows nothing when no report was left out", async () => {
    vi.mocked(api.get).mockResolvedValueOnce([]);
    const { container } = renderWithAppProviders(<LeftOutReportsNotice domainId="d1" types={["TLS-RPT"]} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
