import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithAppProviders } from "../../test/render";
import AdminUpdates from "./AdminUpdates";

const status = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const actions = vi.hoisted(() => ({ rehearse: vi.fn() }));
vi.mock("../../hooks/useAdmin", () => ({
  useAdminUpdates: () => ({ data: status.value, isLoading: false }),
  useAdminUpdateActions: () => ({
    setPrereleases: { mutate: vi.fn() },
    checkNow: { mutate: vi.fn(), mutateAsync: vi.fn() },
    triggerUpdate: { mutate: vi.fn(), mutateAsync: vi.fn() },
    rehearse: actions.rehearse,
  }),
}));

const BASE = {
  running_version: "v0.1.5-beta14", latest_version: "v0.1.5-rc1", latest_release_url: null,
  latest_release_notes: "notes", latest_published_at: null, checked_at: null, check_error: null,
  include_prereleases: true, update_available: true, is_dev_build: false, rehearsal_available: false,
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
  });

  it("shows Portainer steps on a Portainer stack without Update now", () => {
    status.value = { ...BASE, self_update_available: false, deployment_platform: "portainer", azure_resource_group: null };
    renderWithAppProviders(<AdminUpdates />);

    expect(screen.getByText(/Update the stack/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Deploying with Portainer" })).toBeInTheDocument();
    expect(screen.queryByText(/docker compose pull/)).not.toBeInTheDocument();
  });

  it("keeps Update now where an updater is configured", () => {
    status.value = { ...BASE, self_update_available: true, deployment_platform: null, azure_resource_group: null };
    renderWithAppProviders(<AdminUpdates />);

    expect(screen.getByRole("button", { name: "Update now" })).toBeInTheDocument();
  });

  it("offers a test update on Azure and says where to see the result", async () => {
    actions.rehearse.mockResolvedValueOnce({});
    status.value = {
      ...BASE, update_available: false, self_update_available: true, rehearsal_available: true,
      deployment_platform: "azure-container-apps", azure_resource_group: "rg-yadt",
    };
    renderWithAppProviders(<AdminUpdates />);

    screen.getByRole("button", { name: "Run test update" }).click();

    expect(await screen.findByText(/Test update started/)).toHaveTextContent("rg-yadt");
    expect(actions.rehearse).toHaveBeenCalledOnce();
  });

  it("doesn't offer a test update elsewhere", () => {
    status.value = { ...BASE, self_update_available: true, deployment_platform: null, azure_resource_group: null };
    renderWithAppProviders(<AdminUpdates />);

    expect(screen.queryByRole("button", { name: "Run test update" })).not.toBeInTheDocument();
  });
});

