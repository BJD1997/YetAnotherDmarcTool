import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import SignInTab from "./SignInTab";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const NONE = {
  provider: "none", slug: "k7m2x9qp4w", login_link: "https://dmarc.example/login/k7m2x9qp4w",
  redirect_uri: "https://dmarc.example/api/auth/oidc/callback", entra_tenant_id: null, google_workspace_domain: null, oidc: null,
  available: { microsoft: true, google: false, oidc: true }, can_change: true,
};

function passTest(data: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { origin: window.location.origin, data: { type: "yadt-sign-in-test", ok: true, ...data } }));
  });
}

beforeEach(() => {
  vi.mocked(api.get).mockReset().mockResolvedValue(NONE);
  vi.mocked(api.post).mockReset().mockResolvedValue({ start_url: "/api/auth/sign-in-test/start" });
  vi.mocked(api.put).mockReset().mockResolvedValue({ ...NONE, provider: "oidc" });
  vi.spyOn(window, "open").mockReturnValue(null);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("SignInTab", () => {
  it("saves an OIDC provider only after a passed test, with its ticket", async () => {
    renderWithAppProviders(<SignInTab />);
    expect(await screen.findByText("https://dmarc.example/login/k7m2x9qp4w")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Issuer URL"), { target: { value: "https://idp.acme.example" } });
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "yadt" } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "s3cret" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Test sign-in" }));
    await waitFor(() => expect(window.open).toHaveBeenCalledWith("/api/auth/sign-in-test/start", "yadt-sign-in-test", expect.any(String)));
    expect(api.post).toHaveBeenCalledWith("/organizations/current/sign-in/test", {
      provider: "oidc", issuer: "https://idp.acme.example", client_id: "yadt", client_secret: "s3cret",
    });

    passTest({ ticket: "t1", email: "jan@acme.example" });
    expect(await screen.findByText(/Signed in as jan@acme.example/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith("/organizations/current/sign-in", expect.objectContaining({ provider: "oidc", ticket: "t1" })),
    );
  });

  it("tests Microsoft without any fields and shows the tenant it found", async () => {
    renderWithAppProviders(<SignInTab />);
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "microsoft" } });
    expect(screen.queryByLabelText("Issuer URL")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Test sign-in" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/organizations/current/sign-in/test", { provider: "microsoft" }));
    passTest({ ticket: "t2", email: "jan@acme.example", tenant_id: "11111111-2222" });
    expect(await screen.findByText(/tenant 11111111-2222/)).toBeInTheDocument();
  });

  it("offers only the providers this server has", async () => {
    renderWithAppProviders(<SignInTab />);
    const google = await screen.findByRole("option", { name: /Google Workspace/ });
    expect(google).toBeDisabled();
  });

  it("ignores test results from other origins", async () => {
    renderWithAppProviders(<SignInTab />);
    await screen.findByLabelText("Issuer URL");
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { origin: "https://evil.example", data: { type: "yadt-sign-in-test", ok: true, ticket: "x", email: "e" } }));
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("is read-only for everyone but the break-glass account", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...NONE, provider: "microsoft", can_change: false });
    renderWithAppProviders(<SignInTab />);
    expect(await screen.findByText(/Only your organization's break-glass account can change this/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Test sign-in" })).not.toBeInTheDocument();
  });
});
