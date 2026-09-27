import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import AdminSessionChooser from "./AdminSessionChooser";

const refetch = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth/AdminAuthContext", () => ({ useAdminAuth: () => ({ refetch }) }));

describe("AdminSessionChooser", () => {
  it("lets you pick between the organization sign-in and the break-glass login", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      local: { email: "same@example.com", organization_name: null },
      operator_org: { email: "same@example.com", organization_name: "Davids Hosting" },
    });
    vi.mocked(api.post).mockResolvedValueOnce(undefined);

    renderWithAppProviders(<AdminSessionChooser />);

    expect(await screen.findByText("same@example.com · Davids Hosting")).toBeInTheDocument();
    expect(screen.getByText("Break-glass admin login")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Your organization's sign-in/ }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/session-choice", { choice: "operator_org" }));
    await waitFor(() => expect(refetch).toHaveBeenCalled());
  });
});
