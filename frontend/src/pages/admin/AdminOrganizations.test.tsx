import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import AdminOrganizations from "./AdminOrganizations";

vi.mock("../../api/client", () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }));
vi.mock("../../auth/AdminAuthContext", () => ({
  useAdminAuth: () => ({ admin: { auth_type: "entra" } }),
}));
const getMock = vi.mocked(api.get);
beforeEach(() => {
  // Note: must be a block body, not `() => getMock.mockReset()` — mockReset()
  // returns the mock itself, and Vitest treats a function returned from
  // beforeEach as an implicit afterEach cleanup, which would then invoke
  // this mock a second time with no arguments after the test runs.
  getMock.mockReset();
});
// Some tests below use fake timers to drive the search debounce; always
// restore real ones afterward so a failure mid-test can't leak fake timers
// into later tests.
afterEach(() => vi.useRealTimers());

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

  it("shows the empty-state message when the API returns zero organizations", async () => {
    getMock.mockResolvedValueOnce({
      organizations: [],
      has_more: false,
      summary: { total: 0, active: 0, suspended: 0, orgs_with_job_errors_7d: 0 },
    });

    renderWithAppProviders(<AdminOrganizations />);

    expect(await screen.findByText("No organizations match your search.")).toBeInTheDocument();
  });

  it("shows a loading state before data arrives", () => {
    getMock.mockImplementation(() => new Promise(() => {})); // never resolves — stays "loading" for the test

    renderWithAppProviders(<AdminOrganizations />);

    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("clicking Load more fetches a second page with the correct before_id", async () => {
    getMock.mockImplementation(async (url: string) => {
      if (url.includes("before_id=Alpha")) {
        return { organizations: [org("Beta")], has_more: false, summary: null };
      }
      return {
        organizations: [org("Alpha")],
        has_more: true,
        summary: { total: 2, active: 2, suspended: 0, orgs_with_job_errors_7d: 0 },
      };
    });

    renderWithAppProviders(<AdminOrganizations />);

    await waitFor(() => expect(screen.getByText("Load more")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Load more"));

    await waitFor(() => expect(screen.queryByText("Load more")).not.toBeInTheDocument());
    expect(getMock).toHaveBeenCalledWith(expect.stringContaining("before_id=Alpha"));
  });

  it("debounces the search input so rapid typing fires only one additional query", async () => {
    getMock.mockResolvedValue({ organizations: [], has_more: false, summary: { total: 0, active: 0, suspended: 0, orgs_with_job_errors_7d: 0 } });

    renderWithAppProviders(<AdminOrganizations />);
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1)); // the initial, unfiltered load

    vi.useFakeTimers();
    const input = screen.getByPlaceholderText("Search organizations by name");
    act(() => {
      fireEvent.change(input, { target: { value: "a" } });
      fireEvent.change(input, { target: { value: "ac" } });
      fireEvent.change(input, { target: { value: "acm" } });
      fireEvent.change(input, { target: { value: "acme" } });
    });
    // The input itself must reflect every keystroke immediately, even
    // though the query is still debouncing.
    expect(input).toHaveValue("acme");

    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(getMock).toHaveBeenCalledTimes(1); // debounce window hasn't elapsed yet

    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    vi.useRealTimers();

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2)); // exactly one debounced query, not four
    expect(getMock).toHaveBeenLastCalledWith(expect.stringContaining("search=acme"));
  });

  it("grants platform admin access to an organization after confirmation", async () => {
    getMock.mockResolvedValue({
      organizations: [org("Alpha")],
      has_more: false,
      summary: { total: 1, active: 1, suspended: 0, orgs_with_job_errors_7d: 0 },
    });
    vi.mocked(api.patch).mockResolvedValueOnce(org("Alpha", { is_operator: true }));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithAppProviders(<AdminOrganizations />);
    fireEvent.click(await screen.findByText("Alpha"));
    fireEvent.click(await screen.findByRole("button", { name: "Make platform admin organization" }));

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("see and manage every organization"));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/admin/organizations/Alpha", { is_operator: true }));
    confirmSpy.mockRestore();
  });
});
