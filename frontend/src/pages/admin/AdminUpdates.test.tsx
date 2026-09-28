import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithAppProviders } from "../../test/render";
import AdminUpdates from "./AdminUpdates";

const status = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("../../hooks/useAdmin", () => ({
  useAdminUpdates: () => ({ data: status.value, isLoading: false }),
  useAdminUpdateActions: () => ({
    setPrereleases: { mutate: vi.fn() },
    checkNow: { mutate: vi.fn(), mutateAsync: vi.fn() },
    triggerUpdate: { mutate: vi.fn(), mutateAsync: vi.fn() },
  }),
}));

const BASE = {
  running_version: "v0.1.5-beta14", latest_version: "v0.1.5-rc1", latest_release_url: null,
  latest_release_notes: "notes", latest_published_at: null, checked_at: null, check_error: null,
  include_prereleases: true, update_available: true, is_dev_build: false,
};

describe("AdminUpdates", () => {
  it("shows the Azure redeploy command instead of Update now when there's no updater", () => {
    status.value = { ...BASE, self_update_available: false, deployment_platform: "azure-container-apps", azure_resource_group: "rg-yadt" };
    renderWithAppProviders(<AdminUpdates />);

    expect(screen.queryByRole("button", { name: "Update now" })).not.toBeInTheDocument();
    expect(screen.getByText(/az deployment group create -g rg-yadt/)).toHaveTextContent(
      "YetAnotherDmarcTool/v0.1.5-rc1/deploy/azure/azuredeploy.json",
    );
    expect(screen.getByText(/az deployment group create/)).toHaveTextContent("imageTag=v0.1.5-rc1");
  });

  it("shows server steps for a Docker Compose instance without an updater", () => {
    status.value = { ...BASE, self_update_available: false, deployment_platform: null, azure_resource_group: null };
    renderWithAppProviders(<AdminUpdates />);

    expect(screen.getByText(/IMAGE_TAG=v0.1.5-rc1/)).toBeInTheDocument();
    expect(screen.getByText(/On Portainer/)).toBeInTheDocument();
  });

  it("keeps Update now where an updater is configured", () => {
    status.value = { ...BASE, self_update_available: true, deployment_platform: null, azure_resource_group: null };
    renderWithAppProviders(<AdminUpdates />);

    expect(screen.getByRole("button", { name: "Update now" })).toBeInTheDocument();
  });
});
