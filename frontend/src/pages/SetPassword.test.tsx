import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import SetPassword from "./SetPassword";

vi.mock("../api/client", () => ({
  api: { post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ refetch: vi.fn() }) }));

describe("SetPassword", () => {
  it("asks for the existing authenticator's code after an admin password reset", async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ needs_enrollment: false }).mockResolvedValueOnce(undefined);
    renderWithAppProviders(<SetPassword />, { route: "/set-password?token=tok" });

    fireEvent.change(screen.getByPlaceholderText(/New password/), { target: { value: "a brand new password" } });
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "a brand new password" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    fireEvent.change(await screen.findByPlaceholderText("6-digit code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(api.post).toHaveBeenLastCalledWith("/auth/verify-otp", { code: "123456" }));
    expect(screen.queryByAltText("TOTP QR code")).not.toBeInTheDocument();
  });
});
