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
});
