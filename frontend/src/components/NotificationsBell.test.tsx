import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { renderWithAppProviders } from "../test/render";
import NotificationsBell from "./NotificationsBell";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
const role = vi.hoisted(() => ({ value: "org_admin" }));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: role.value } }) }));
const getMock = vi.mocked(api.get);
const postMock = vi.mocked(api.post);

const ITEM = {
  id: "n1",
  kind: "domain_detected",
  title: "New domain in your reports: other-brand.example",
  detail: "12 messages · add it or dismiss it",
  link_path: "/settings/domains",
  created_at: new Date().toISOString(),
  resolved_at: null,
  resolved_reason: null,
};

function serve(open: number) {
  getMock.mockImplementation(async (url: string) => (url === "/notifications/count" ? { open } : open ? [ITEM] : []));
}

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  role.value = "org_admin";
});

describe("NotificationsBell", () => {
  it("shows a dot only while something is open", async () => {
    serve(1);
    const { unmount } = renderWithAppProviders(<NotificationsBell />);
    expect(await screen.findByTestId("notif-dot")).toBeInTheDocument();
    expect(screen.getByLabelText("Notifications, 1 open")).toBeInTheDocument();
    unmount();

    serve(0);
    renderWithAppProviders(<NotificationsBell />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/notifications/count"));
    expect(screen.queryByTestId("notif-dot")).not.toBeInTheDocument();
  });

  it("lists notifications with links, and admins can dismiss", async () => {
    serve(1);
    postMock.mockResolvedValue(undefined);
    renderWithAppProviders(<NotificationsBell />);

    fireEvent.click(await screen.findByLabelText("Notifications, 1 open"));
    const link = await screen.findByRole("link", { name: /other-brand\.example/ });
    expect(link).toHaveAttribute("href", "/settings/domains");

    fireEvent.click(screen.getByLabelText(`Dismiss: ${ITEM.title}`));
    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/notifications/n1/dismiss"));
  });

  it("has no dismiss button for members, and Escape closes it", async () => {
    role.value = "member";
    serve(1);
    renderWithAppProviders(<NotificationsBell />);

    fireEvent.click(await screen.findByLabelText("Notifications, 1 open"));
    await screen.findByRole("link", { name: /other-brand\.example/ });
    expect(screen.queryByLabelText(`Dismiss: ${ITEM.title}`)).not.toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
