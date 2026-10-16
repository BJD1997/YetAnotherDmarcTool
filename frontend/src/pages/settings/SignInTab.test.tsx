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
  provider: "none", slug: "acme", login_link: "https://dmarc.example/login/acme",
  redirect_uri: "https://dmarc.example/api/auth/oidc/callback", google_workspace_domain: null, oidc: null,
};

beforeEach(() => {
  vi.mocked(api.get).mockReset().mockResolvedValue(NONE);
  vi.mocked(api.post).mockReset().mockResolvedValue({ start_url: "/api/auth/oidc/start-test" });
  vi.mocked(api.put)
    .mockReset()
    .mockResolvedValue({ ...NONE, provider: "oidc", oidc: { issuer: "https://idp.acme.example", client_id: "yadt", secret_set: true } });
  vi.spyOn(window, "open").mockReturnValue(null);
});

describe("SignInTab", () => {
  it("only saves after a passed test, with the ticket it returned", async () => {
    renderWithAppProviders(<SignInTab />);
    expect(await screen.findByText("https://dmarc.example/api/auth/oidc/callback")).toBeInTheDocument();
    expect(screen.getByText("https://dmarc.example/login/acme")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Issuer URL"), { target: { value: "https://idp.acme.example" } });
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "yadt" } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "s3cret" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Test sign-in" }));
    await waitFor(() => expect(window.open).toHaveBeenCalledWith("/api/auth/oidc/start-test", "yadt-oidc-test", expect.any(String)));

    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: window.location.origin,
          data: { type: "yadt-oidc-test", ok: true, ticket: "t1", email: "jan@acme.example" },
        }),
      );
    });
    expect(await screen.findByText(/Signed in as jan@acme.example/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        "/organizations/current/sign-in/oidc",
        expect.objectContaining({ ticket: "t1", issuer: "https://idp.acme.example", client_secret: "s3cret" }),
      ),
    );
  });

  it("ignores test results from other origins", async () => {
    renderWithAppProviders(<SignInTab />);
    await screen.findByLabelText("Issuer URL");
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { origin: "https://evil.example", data: { type: "yadt-oidc-test", ok: true, ticket: "x", email: "e" } }));
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows a Microsoft org's setting as read-only", async () => {
    vi.mocked(api.get).mockResolvedValue({ ...NONE, provider: "microsoft" });
    renderWithAppProviders(<SignInTab />);
    expect(await screen.findByText(/signs in with Microsoft/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Issuer URL")).not.toBeInTheDocument();
  });
});
