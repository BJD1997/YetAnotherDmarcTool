import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Team from "./Team";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "me", role: "org_admin", auth_method: "local" } }),
}));
vi.mock("../hooks/useOrganization", () => ({
  useCurrentOrganization: () => ({ data: { entra_tenant_id: null } }),
}));

function member(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id, email: `${id}@example.com`, display_name: null, role: "member", status: "active",
    auth_method: "local", mfa_enrolled: true, last_login_at: null, ...overrides,
  };
}

describe("Team credential resets", () => {
  it("offers resets only for other local members, and shows the new setup link", async () => {
    vi.mocked(api.get).mockResolvedValue([
      member("me", { role: "org_admin" }),
      member("alice"),
      member("bob", { auth_method: "entra" }),
      member("carol", { mfa_enrolled: false }),
    ]);
    vi.mocked(api.post).mockResolvedValueOnce({ setup_link: "https://dmarc.example/set-password?token=abc" });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithAppProviders(<Team />);
    await screen.findByText("alice@example.com");

    // alice + carol get Reset password; only alice (enrolled) gets Reset MFA; me and bob get neither.
    expect(screen.getAllByRole("button", { name: "Reset password" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Reset MFA" })).toHaveLength(1);
    expect(screen.getByText("no MFA yet")).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "Reset password" })[0]);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/users/alice/reset-password"));
    expect(await screen.findByText("https://dmarc.example/set-password?token=abc")).toBeInTheDocument();
  });
});
