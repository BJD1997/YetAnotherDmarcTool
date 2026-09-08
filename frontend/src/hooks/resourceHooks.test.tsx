import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { makeQueryClientWrapper, makeTestQueryClient } from "../test/render";
import { queryKeys } from "./queryKeys";
import { useDomains, useRankedDomains } from "./useDomains";
import { useMailboxConnection } from "./useMailboxConnection";
import { useOnboardingStatus } from "./useOnboarding";
import { useCurrentOrganization } from "./useOrganization";
import { useUsers } from "./useUsers";
import { useAdminOrganizations, useAdminUpdates } from "./useAdmin";
import { useDomain } from "./useDomains";
import { useDmarcSummary } from "./useDomainInsights";
import { useDnsChecks } from "./useDnsChecks";
import { useDmarcPolicyBuilder, useMtaStsPolicyBuilder, useTlsRptPolicyBuilder } from "./usePolicyBuilders";

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
    expect(queryKeys.dmarcReports.summary("domain-1", "days=30")).toEqual([
      "dmarc-reports-summary",
      "domain-1",
      "days=30",
    ]);
    expect(queryKeys.senderInventory.list("domain-1,domain-2", 90)).toEqual([
      "sender-inventory",
      "domain-1,domain-2",
      90,
    ]);
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
    ["ranked domains", useRankedDomains, "/domains/ranked", []],
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

  it.each([
    ["DMARC summary", useDmarcSummary, "/domains/domain-1/dmarc/summary"],
    ["DNS checks", useDnsChecks, "/domains/domain-1/checks"],
    ["DMARC policy builder", useDmarcPolicyBuilder, "/domains/domain-1/dmarc/policy-builder"],
    ["TLS-RPT policy builder", useTlsRptPolicyBuilder, "/domains/domain-1/dns/tls-rpt-builder"],
    ["MTA-STS policy builder", useMtaStsPolicyBuilder, "/domains/domain-1/dns/mta-sts-builder"],
  ])("loads the parameterized %s resource", async (_name, useResource, endpoint) => {
    getMock.mockResolvedValueOnce({});
    const queryClient = makeTestQueryClient();
    const { result } = renderHook(() => useResource("domain-1"), {
      wrapper: makeQueryClientWrapper(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(getMock).toHaveBeenCalledWith(endpoint);
  });
});
