import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import SignInEventsSection from "./SignInEventsSection";

vi.mock("../../api/client", () => ({ api: { get: vi.fn() } }));

describe("SignInEventsSection", () => {
  it("shows who reset whose credentials", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      events: [
        {
          id: "e1", created_at: "2026-09-25T10:00:00Z", result: "account_change", auth_method: "local",
          email: "alice@example.com", failure_reason: "password_reset_by_admin", actor_email: "boss@example.com",
          ip_address: null, user_agent: null,
        },
      ],
      has_more: false,
    });

    renderWithAppProviders(<SignInEventsSection />);

    expect(await screen.findByText("Password reset (by boss@example.com)")).toBeInTheDocument();
    expect(screen.getByText("account change")).toBeInTheDocument();
  });

  it("shows break-glass sign-ins from the admin endpoint, without the method filter", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      events: [
        {
          id: "e2", created_at: "2026-09-27T10:00:00Z", result: "failure", auth_method: "platform_admin",
          email: "admin@example.com", failure_reason: "invalid_credentials", actor_email: null,
          ip_address: "198.51.100.7", user_agent: null,
        },
      ],
      has_more: false,
    });

    renderWithAppProviders(<SignInEventsSection endpoint="/admin/sign-in-events" />);

    expect(await screen.findByText("break-glass")).toBeInTheDocument();
    expect(screen.getByText("invalid_credentials")).toBeInTheDocument();
    expect(vi.mocked(api.get).mock.lastCall?.[0]).toMatch(/^\/admin\/sign-in-events\?/);
    expect(screen.queryByRole("option", { name: "Microsoft" })).not.toBeInTheDocument();
  });
});

