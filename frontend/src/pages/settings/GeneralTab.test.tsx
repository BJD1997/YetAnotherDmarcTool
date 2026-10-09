import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import GeneralTab from "./GeneralTab";

vi.mock("../../api/client", () => ({
  api: { patch: vi.fn(), get: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { role: "org_admin" } }),
}));
const ORG = {
  id: "org-1",
  name: "Org",
  status: "active",
  entra_tenant_id: null,
  is_operator: false,
  spf_all_qualifier_mode: "strict",
  hosted_mailbox_opt_in: false,
  report_sender_check: "standard",
  rating_window_days: 90,
  ask_ai_enabled: false,
  is_demo_read_only: false,
  entra_consent_urls: null,
};
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useOutletContext: () => ORG,
}));
const patchMock = vi.mocked(api.patch);

beforeEach(() => {
  patchMock.mockReset();
  vi.mocked(api.get).mockResolvedValue(null);
});

describe("GeneralTab rating period", () => {
  it("offers 30, 60, 90 and 180 days and saves the choice", async () => {
    patchMock.mockResolvedValue({ ...ORG, rating_window_days: 30 });
    renderWithAppProviders(<GeneralTab />);

    const select = screen.getByLabelText("Rating period") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["30", "60", "90", "180"]);
    expect(select.value).toBe("90");

    fireEvent.change(select, { target: { value: "30" } });
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/organizations/current", { name: "Org", rating_window_days: 30 }),
    );
  });
});

describe("GeneralTab Ask AI", () => {
  it("is off until turned on, and saves the choice", async () => {
    patchMock.mockResolvedValue({ ...ORG, ask_ai_enabled: true });
    renderWithAppProviders(<GeneralTab />);

    const box = screen.getByLabelText("Show Ask AI buttons for this organization") as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    await waitFor(() =>
      expect(patchMock).toHaveBeenCalledWith("/organizations/current", { name: "Org", ask_ai_enabled: true }),
    );
  });
});
