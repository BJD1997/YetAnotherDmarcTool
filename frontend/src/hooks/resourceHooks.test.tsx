import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { makeQueryClientWrapper, makeTestQueryClient } from "../test/render";
import { queryKeys } from "./queryKeys";
import { useDomains } from "./useDomains";
import { useMailboxConnection } from "./useMailboxConnection";
import { useOnboardingStatus } from "./useOnboarding";
import { useCurrentOrganization } from "./useOrganization";

vi.mock("../api/client", () => ({
  api: { get: vi.fn() },
}));

const getMock = vi.mocked(api.get);

beforeEach(() => getMock.mockReset());

describe("canonical query keys", () => {
  it("keeps list and parameterized resource keys structurally related", () => {
    expect(queryKeys.domains.ranked(30)).toEqual(["domains", "ranked", 30]);
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
});
