import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import AskAiInvite from "./AskAiInvite";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), patch: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
const role = vi.hoisted(() => ({ value: "org_admin" }));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: role.value } }) }));
const getMock = vi.mocked(api.get);
const patchMock = vi.mocked(api.patch);

const setup = vi.hoisted(() => ({ done: true }));

function org(ask_ai_enabled: boolean | null) {
  getMock.mockImplementation(async (url: string) =>
    url === "/onboarding/status"
      ? { has_mailbox: setup.done, has_verified_domain: setup.done }
      : { name: "Org", ask_ai_enabled, is_demo_read_only: false },
  );
}

beforeEach(() => {
  getMock.mockReset();
  patchMock.mockReset().mockResolvedValue({});
  role.value = "org_admin";
  setup.done = true;
});

describe("AskAiInvite", () => {
  it("asks an org admin while unanswered; closing it saves no", async () => {
    org(null);
    renderWithAppProviders(<AskAiInvite />);
    fireEvent.click(await screen.findByLabelText("No thanks"));
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/organizations/current", { name: "Org", ask_ai_enabled: false }));
  });

  it("turns it on", async () => {
    org(null);
    renderWithAppProviders(<AskAiInvite />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(patchMock).toHaveBeenCalledWith("/organizations/current", { name: "Org", ask_ai_enabled: true }));
  });

  it("stays away once answered, and from members", async () => {
    org(false);
    const { unmount } = renderWithAppProviders(<AskAiInvite />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(screen.queryByText(/Get help from an AI assistant/)).not.toBeInTheDocument();
    unmount();

    role.value = "member";
    org(null);
    renderWithAppProviders(<AskAiInvite />);
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    expect(screen.queryByText(/Get help from an AI assistant/)).not.toBeInTheDocument();
  });

  it("waits until setup is done", async () => {
    setup.done = false;
    org(null);
    renderWithAppProviders(<AskAiInvite />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/onboarding/status"));
    expect(screen.queryByText(/Get help from an AI assistant/)).not.toBeInTheDocument();
  });
});
