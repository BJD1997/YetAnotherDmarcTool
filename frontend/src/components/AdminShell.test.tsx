import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithAppProviders } from "../test/render";
import AdminShell from "./AdminShell";

type Admin = { email: string; auth_type: "local" | "operator_org"; organization_name: string | null; can_switch: boolean };
const auth = vi.hoisted(() => ({ admin: null as null | Admin, refetch: vi.fn() }));
vi.mock("../auth/AdminAuthContext", () => ({ useAdminAuth: () => ({ admin: auth.admin, refetch: auth.refetch }) }));
vi.mock("../api/client", () => ({ api: { post: vi.fn().mockResolvedValue(undefined) } }));
vi.mock("../hooks/useAdmin", () => ({ useAdminUpdates: () => ({ data: undefined }) }));
vi.mock("../hooks/useOverviewResources", () => ({ useHealth: () => ({ data: undefined }) }));

describe("AdminShell", () => {
  // The break-glass login and an organization's own sign-in can share one
  // email address, so the sidebar has to say which one is active.
  it("labels a break-glass session", () => {
    auth.admin = { email: "admin@example.com", auth_type: "local", organization_name: null, can_switch: false };
    renderWithAppProviders(<AdminShell>content</AdminShell>);

    expect(screen.getByText("Break-glass admin login")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("labels an organization sign-in", () => {
    auth.admin = { email: "admin@example.com", auth_type: "operator_org", organization_name: "Ops", can_switch: false };
    renderWithAppProviders(<AdminShell>content</AdminShell>);

    expect(screen.getByText("Your organization's sign-in")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to dashboard/ })).toBeInTheDocument();
  });

  it("offers to switch when both sign-ins are present", async () => {
    const { api } = await import("../api/client");
    auth.admin = { email: "admin@example.com", auth_type: "local", organization_name: null, can_switch: true };
    renderWithAppProviders(<AdminShell>content</AdminShell>);

    screen.getByRole("button", { name: "Switch" }).click();

    await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/session-choice", { choice: null }));
    await vi.waitFor(() => expect(auth.refetch).toHaveBeenCalled());
  });
});
