import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import Login from "./Login";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ refetch: vi.fn() }) }));
const getMock = vi.mocked(api.get);
const postMock = vi.mocked(api.post);

beforeEach(() => {
  getMock.mockReset().mockResolvedValue({ entra_sso_enabled: true, google_sso_enabled: true, company_sign_in_enabled: true });
  postMock.mockReset();
});

describe("Login", () => {
  it("offers Microsoft, Google, a company account and a password", async () => {
    renderWithAppProviders(<Login />);
    expect(await screen.findByText("Sign in with Microsoft")).toBeInTheDocument();
    expect(screen.getByText("Sign in with Google")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("you@company.com")).toBeInTheDocument();
    expect(screen.getByText(/email and password instead/)).toBeInTheDocument();
  });

  it("says so when an email has no company sign-in", async () => {
    postMock.mockResolvedValueOnce({ url: null });
    renderWithAppProviders(<Login />);
    fireEvent.change(await screen.findByPlaceholderText("you@company.com"), { target: { value: "jan@unknown.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue with company account" }));
    expect(await screen.findByText(/no company sign-in for this email address/)).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/auth/discover", { email: "jan@unknown.example" });
  });

  it("only shows what the server offers", async () => {
    getMock.mockResolvedValue({ entra_sso_enabled: false, google_sso_enabled: false, company_sign_in_enabled: true });
    renderWithAppProviders(<Login />);
    expect(await screen.findByPlaceholderText("you@company.com")).toBeInTheDocument();
    expect(screen.queryByText("Sign in with Microsoft")).not.toBeInTheDocument();
    expect(screen.queryByText("Sign in with Google")).not.toBeInTheDocument();
  });

  it("goes straight to email and password when the server has no providers", async () => {
    getMock.mockResolvedValue({ entra_sso_enabled: false, google_sso_enabled: false, company_sign_in_enabled: false });
    renderWithAppProviders(<Login />);
    await waitFor(() => expect(screen.getByPlaceholderText("Password")).toBeInTheDocument());
  });

  it("explains a provider error from the redirect", async () => {
    renderWithAppProviders(<Login />, { route: "/login?error=email_domain_not_verified" });
    expect(await screen.findByText(/isn't on one of your organization's verified domains/)).toBeInTheDocument();
  });
});
