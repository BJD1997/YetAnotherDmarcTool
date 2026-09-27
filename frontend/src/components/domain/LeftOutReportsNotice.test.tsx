import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import LeftOutReportsNotice from "./LeftOutReportsNotice";

vi.mock("../../api/client", () => ({ api: { get: vi.fn() } }));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "org_admin" } }) }));

const REPORTS = [
  { id: "a", type: "DMARC", reporter: "google.com", period_start: "2026-09-20T00:00:00Z", received_at: "2026-09-21T08:00:00Z", sender_domain: "evil.example", reason: "Sent by evil.example, not by the reporter it names" },
  { id: "b", type: "TLS-RPT", reporter: "Google Inc.", period_start: "2026-09-20T00:00:00Z", received_at: "2026-09-21T09:00:00Z", sender_domain: "google.com", reason: "Failed DMARC for google.com" },
];

describe("LeftOutReportsNotice", () => {
  it("counts this tab's left-out reports and lists them with the reason", async () => {
    vi.mocked(api.get).mockResolvedValueOnce(REPORTS);
    renderWithAppProviders(<LeftOutReportsNotice domainId="d1" types={["DMARC", "Forensic"]} />);

    expect(await screen.findByText(/1 report was left out/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review" }));

    expect(screen.getByText("Sent by evil.example, not by the reporter it names")).toBeInTheDocument();
    expect(screen.queryByText("Failed DMARC for google.com")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "report sender check" })).toHaveAttribute("href", "/settings");
  });

  it("shows nothing when no report was left out", async () => {
    vi.mocked(api.get).mockResolvedValueOnce([]);
    const { container } = renderWithAppProviders(<LeftOutReportsNotice domainId="d1" types={["TLS-RPT"]} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container).toBeEmptyDOMElement();
  });
});
