import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { Organization } from "../../api/types";
import type { PolicyBuilderData } from "../../api/policyBuilder";
import { makeTestQueryClient } from "../../test/render";
import PolicyBuilder from "./PolicyBuilder";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const getMock = vi.mocked(api.get);

const ORG: Organization = {
  id: "org-1",
  name: "Test Org",
  status: "active",
  entra_tenant_id: null,
  is_operator: false,
  spf_all_qualifier_mode: "strict",
  hosted_mailbox_opt_in: false,
  entra_consent_urls: null,
};

function builderData(overrides: Partial<PolicyBuilderData> = {}): PolicyBuilderData {
  return {
    current_record: null,
    current_record_lookup_error: false,
    rua_destination: { status: "correct", current_targets: [] },
    org_mailbox_address: "reports@example.com",
    hosted_report_address: null,
    policy_stability_days: 0,
    recommendation: { policy: "none", np: null, reasoning: "No reports yet.", blocked: false, blocking_reason: null },
    ...overrides,
  };
}

async function renderBuilder(data: PolicyBuilderData) {
  getMock.mockImplementation(async (url: string) => {
    if (url.includes("/dmarc/policy-builder")) return data;
    if (url.includes("/organizations/current")) return ORG;
    throw new Error(`unexpected URL in test: ${url}`);
  });
  const queryClient = makeTestQueryClient();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <PolicyBuilder domainId="domain-1" domainName="web02.example.com" onClose={() => {}} />
    </QueryClientProvider>,
  );
  // "Enforcement" only renders once `data` has loaded — the modal header
  // above it renders during the loading state too, so waiting on that
  // instead would race ahead of the seeding effect this file is testing.
  await screen.findByText("Enforcement");
  return utils;
}

beforeEach(() => {
  getMock.mockReset();
});

describe("PolicyBuilder seeding", () => {
  it("carries over np= (and other existing tags) from the current record instead of the recommendation", async () => {
    await renderBuilder(
      builderData({
        current_record: {
          raw: "v=DMARC1; p=none; np=reject; adkim=r; aspf=r; rua=mailto:reports@example.com",
          tags: { v: "DMARC1", p: "none", np: "reject", adkim: "r", aspf: "r", rua: "mailto:reports@example.com" },
        },
        // The recommendation deliberately disagrees with np= to prove the
        // form seeds from current_record, not from this.
        recommendation: { policy: "none", np: null, reasoning: "No reports yet.", blocked: false, blocking_reason: null },
      }),
    );

    expect(screen.getByTestId("generated-record").textContent).toContain("np=reject");
  });

  it("carries over sp= and strict alignment from the current record", async () => {
    await renderBuilder(
      builderData({
        current_record: {
          raw: "v=DMARC1; p=reject; sp=quarantine; adkim=s; aspf=s",
          tags: { v: "DMARC1", p: "reject", sp: "quarantine", adkim: "s", aspf: "s" },
        },
        org_mailbox_address: null,
      }),
    );

    expect(screen.getByDisplayValue("Quarantine")).toBeInTheDocument(); // the sp= select
    // Advanced alignment starts collapsed — open it before checking adkim/aspf.
    fireEvent.click(screen.getByText("Show advanced alignment settings"));
    const strictSelects = screen.getAllByDisplayValue("Strict");
    expect(strictSelects).toHaveLength(2); // adkim and aspf
    expect(screen.getByTestId("generated-record").textContent).toBe("v=DMARC1; p=reject; sp=quarantine; adkim=s; aspf=s");
  });

  it("falls back to the recommendation's policy/np when there is no current record at all", async () => {
    await renderBuilder(
      builderData({
        current_record: null,
        org_mailbox_address: null,
        recommendation: { policy: "quarantine", np: "reject", reasoning: "Ready.", blocked: false, blocking_reason: null },
      }),
    );

    expect(screen.getByTestId("generated-record").textContent).toBe("v=DMARC1; p=quarantine; np=reject; adkim=r; aspf=r");
  });

  it("does not warn when the seeded state exactly matches the current record", async () => {
    await renderBuilder(
      builderData({
        current_record: { raw: "v=DMARC1; p=reject; np=reject", tags: { v: "DMARC1", p: "reject", np: "reject" } },
      }),
    );

    expect(screen.queryByText("This weakens your current policy")).not.toBeInTheDocument();
  });

  it("warns when the user weakens an already-published policy", async () => {
    await renderBuilder(
      builderData({
        current_record: { raw: "v=DMARC1; p=reject; np=reject", tags: { v: "DMARC1", p: "reject", np: "reject" } },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Monitor only" }));

    expect(await screen.findByText("This weakens your current policy")).toBeInTheDocument();
    expect(screen.getByText("p=reject → p=none")).toBeInTheDocument();
  });
});
