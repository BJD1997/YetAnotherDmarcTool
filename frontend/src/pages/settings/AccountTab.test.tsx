import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import AccountTab from "./AccountTab";

vi.mock("../../api/client", () => ({
  api: { post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
const authMethod = vi.hoisted(() => ({ value: "local" as "local" | "entra" }));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { auth_method: authMethod.value } }),
}));
const outlet = vi.hoisted(() => ({ org: undefined as { is_demo_read_only: boolean } | undefined }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useOutletContext: () => outlet.org,
}));
const postMock = vi.mocked(api.post);

beforeEach(() => {
  postMock.mockReset();
  authMethod.value = "local";
  outlet.org = undefined;
});

describe("AccountTab", () => {
  it("points SSO users to Microsoft instead of showing credential forms", () => {
    authMethod.value = "entra";
    renderWithAppProviders(<AccountTab />);

    expect(screen.getByText(/managed by your organization's Microsoft Entra account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change password" })).not.toBeInTheDocument();
  });

  it("changes the password with the current one", async () => {
    postMock.mockResolvedValueOnce(undefined);
    renderWithAppProviders(<AccountTab />);

    fireEvent.change(screen.getByPlaceholderText("Current password"), { target: { value: "old password here" } });
    fireEvent.change(screen.getByPlaceholderText(/New password/), { target: { value: "a brand new password" } });
    fireEvent.change(screen.getByPlaceholderText("Confirm new password"), { target: { value: "a brand new password" } });
    fireEvent.click(screen.getByRole("button", { name: "Change password" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/auth/change-password", {
        current_password: "old password here",
        new_password: "a brand new password",
      }),
    );
    expect(await screen.findByText(/Password changed/)).toBeInTheDocument();
  });

  it("replaces the authenticator and shows new recovery codes", async () => {
    postMock
      .mockResolvedValueOnce({ secret: "NEWSECRET", qr_code_data_uri: "data:image/png;base64," })
      .mockResolvedValueOnce({ recovery_codes: ["aaaa-bbbb", "cccc-dddd"] });
    renderWithAppProviders(<AccountTab />);

    fireEvent.click(screen.getByRole("button", { name: "Replace authenticator" }));
    fireEvent.change(screen.getByPlaceholderText(/Confirm with your current password/), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.change(await screen.findByPlaceholderText("6-digit code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm new authenticator" }));

    expect(await screen.findByText("aaaa-bbbb")).toBeInTheDocument();
    expect(postMock).toHaveBeenLastCalledWith("/auth/mfa/reset/confirm", {
      current_password: "pw",
      secret: "NEWSECRET",
      code: "123456",
    });
  });

  it("doesn't offer credential changes on the shared demo account", () => {
    outlet.org = { is_demo_read_only: true };
    renderWithAppProviders(<AccountTab />);

    expect(screen.getByText(/shared demo account/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change password" })).not.toBeInTheDocument();
  });
});
