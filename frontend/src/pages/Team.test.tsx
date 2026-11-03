import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Team from "./Team";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "me", role: "org_admin", auth_method: "local" } }),
}));
const currentOrg = vi.hoisted(() => ({ value: { entra_tenant_id: null, sign_in_provider: "none" } as Record<string, unknown> }));
vi.mock("../hooks/useOrganization", () => ({
  useCurrentOrganization: () => ({ data: currentOrg.value }),
}));

function member(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id, email: `${id}@example.com`, display_name: null, role: "member", status: "active",
    auth_method: "local", mfa_enrolled: true, last_login_at: null, removable: false, ...overrides,
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

describe("Team account removal", () => {
  it("labels the account a provider switch left behind and removes only that one", async () => {
    vi.mocked(api.get).mockResolvedValue([
      member("me", { role: "org_admin", auth_method: "entra" }),
      member("old", { email: "me@example.com", auth_method: "oidc", removable: true }),
      member("dave", { auth_method: "entra" }),
    ]);
    vi.mocked(api.delete).mockResolvedValueOnce(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithAppProviders(<Team />);
    await screen.findByText("dave@example.com");

    expect(screen.getByText("OpenID Connect")).toBeInTheDocument();
    expect(screen.getByText("previous provider")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/users/old"));
    expect(await screen.findByText("Removed the account of me@example.com.")).toBeInTheDocument();
  });
});

describe("Team invites", () => {
  it("shares the organization's own sign-in link, and adds no local teammates next to a provider", async () => {
    currentOrg.value = { entra_tenant_id: null, sign_in_provider: "google" };
    vi.mocked(api.get).mockImplementation(async (url: string) =>
      url === "/users" ? [] : { login_link: "https://dmarc.example/login/acme", provider: "google" },
    );
    renderWithAppProviders(<Team />);

    expect(await screen.findByText("https://dmarc.example/login/acme")).toBeInTheDocument();
    expect(screen.getByText(/Google Workspace account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Generate setup link/ })).not.toBeInTheDocument();
    currentOrg.value = { entra_tenant_id: null, sign_in_provider: "none" };
  });
});
