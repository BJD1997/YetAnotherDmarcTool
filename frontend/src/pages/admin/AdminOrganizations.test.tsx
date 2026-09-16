import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import AdminOrganizations from "./AdminOrganizations";

vi.mock("../../api/client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
vi.mock("../../auth/AdminAuthContext", () => ({
  useAdminAuth: () => ({ admin: { auth_type: "entra" } }),
}));
const getMock = vi.mocked(api.get);
beforeEach(() => getMock.mockReset());

function org(name: string, overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: name, name, entra_tenant_id: null, status: "active", is_operator: false, created_at: "2026-01-01T00:00:00Z",
    mailbox_connection: null, entra_consent_urls: null, domain_count: 0, job_error_count_7d: 0, last_report_at: null,
    ...overrides,
  };
}

describe("AdminOrganizations", () => {
  it("shows the glanceable summary bar and a compact row per org, collapsed by default", async () => {
    getMock.mockResolvedValueOnce({
      organizations: [org("Alpha"), org("Beta")],
      has_more: false,
      // total (2) intentionally distinct from active/suspended/job-error counts below,
      // otherwise the "2" text is ambiguous (matches both the total and active Stat tiles).
      summary: { total: 2, active: 1, suspended: 1, orgs_with_job_errors_7d: 0 },
    });

    renderWithAppProviders(<AdminOrganizations />);

    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument());
    expect(screen.getByText("2")).toBeInTheDocument(); // total in summary bar
    expect(screen.queryByPlaceholderText("tenant GUID")).not.toBeInTheDocument(); // detail hidden until expanded
  });

  it("expands a row's detail on click", async () => {
    getMock.mockResolvedValueOnce({
      organizations: [org("Alpha")],
      has_more: false,
      summary: { total: 1, active: 1, suspended: 0, orgs_with_job_errors_7d: 0 },
    });

    renderWithAppProviders(<AdminOrganizations />);

    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Alpha"));
    expect(await screen.findByPlaceholderText("tenant GUID")).toBeInTheDocument();
  });

  it("restarts the query with the search term", async () => {
    getMock.mockResolvedValue({ organizations: [], has_more: false, summary: { total: 0, active: 0, suspended: 0, orgs_with_job_errors_7d: 0 } });

    renderWithAppProviders(<AdminOrganizations />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText("Search organizations by name"), { target: { value: "acme" } });
    await waitFor(() => expect(getMock).toHaveBeenLastCalledWith(expect.stringContaining("search=acme")));
  });
});
