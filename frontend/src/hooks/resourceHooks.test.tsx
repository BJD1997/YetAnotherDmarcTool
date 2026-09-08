import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { makeQueryClientWrapper, makeTestQueryClient } from "../test/render";
import { queryKeys } from "./queryKeys";
import { useDomains } from "./useDomains";
import { useMailboxConnection } from "./useMailboxConnection";
import { useOnboardingStatus } from "./useOnboarding";
import { useCurrentOrganization } from "./useOrganization";
import { useUsers } from "./useUsers";
import { useAdminOrganizations, useAdminUpdates } from "./useAdmin";
import { useDomain } from "./useDomains";

vi.mock("../api/client", () => ({
  api: { get: vi.fn() },
}));

const getMock = vi.mocked(api.get);

beforeEach(() => getMock.mockReset());

describe("canonical query keys", () => {
  it("keeps list and parameterized resource keys structurally related", () => {
    expect(queryKeys.domains.ranked).toEqual(["domains-ranked"]);
    expect(queryKeys.domains.detail("domain-1")).toEqual(["domains", "domain-1"]);
    expect(queryKeys.organization.current).toEqual(["organization", "current"]);
  });
});

describe("shared resource hooks", () => {
  it.each([
    ["domains", useDomains, "/domains", []],
    ["organization", useCurrentOrganization, "/organizations/current", { id: "org-1" }],
    ["mailbox", useMailboxConnection, "/mailbox-connection", { mailbox_address: "reports@example.com" }],
    ["onboarding", useOnboardingStatus, "/onboarding/status", { has_mailbox: true }],
    ["users", useUsers, "/users", []],
    ["admin organizations", useAdminOrganizations, "/admin/organizations", []],
    ["admin updates", useAdminUpdates, "/admin/updates", { update_available: false }],
  ])("loads the %s resource from its canonical endpoint", async (_name, useResource, endpoint, response) => {
    getMock.mockResolvedValueOnce(response);
    const queryClient = makeTestQueryClient();

    const { result } = renderHook(() => useResource(), {
      wrapper: makeQueryClientWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getMock).toHaveBeenCalledOnce();
    expect(getMock).toHaveBeenCalledWith(endpoint);
    expect(result.current.data).toEqual(response);
  });

  it("loads a domain detail using its ID", async () => {
    getMock.mockResolvedValueOnce({ id: "domain-1", name: "example.com" });
    const queryClient = makeTestQueryClient();
    const { result } = renderHook(() => useDomain("domain-1"), {
      wrapper: makeQueryClientWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getMock).toHaveBeenCalledWith("/domains/domain-1");
    expect(result.current.data).toMatchObject({ id: "domain-1" });
  });
});
