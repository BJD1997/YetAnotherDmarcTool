import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { SpfBuilderData, SpfEvaluation } from "../../api/dnsChecks";
import { makeTestQueryClient } from "../../test/render";
import SpfRecordBuilder from "./SpfRecordBuilder";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const getMock = vi.mocked(api.get);
const postMock = vi.mocked(api.post);

const PROVIDERS = [
  { key: "microsoft365", label: "Microsoft 365", include: "spf.protection.outlook.com" },
  { key: "icloud", label: "iCloud Mail", include: "icloud.com" },
  { key: "smtp2go", label: "SMTP2GO", include: "spf.smtp2go.com" },
];

function builderData(overrides: Partial<SpfBuilderData> = {}): SpfBuilderData {
  return {
    current_record: "v=spf1 include:spf.protection.outlook.com include:mail.example.net ~all",
    current_record_count: 1,
    current_record_lookup_error: false,
    providers: PROVIDERS,
    suggestions: [],
    ...overrides,
  };
}

function evaluation(overrides: Partial<SpfEvaluation> = {}): SpfEvaluation {
  return { lookup_count: 2, limit: 10, capped: false, void_lookup_count: 0, void_limit: 2, notes: [], terms: [], ...overrides };
}

function renderBuilder(data: SpfBuilderData, counted: SpfEvaluation = evaluation()) {
  getMock.mockResolvedValue(data);
  postMock.mockResolvedValue(counted);
  render(
    <QueryClientProvider client={makeTestQueryClient()}>
      <SpfRecordBuilder domainId="domain-1" domainName="example.com" onClose={() => {}} />
    </QueryClientProvider>,
  );
}

// The generated record comes first; the published one is listed under it.
function generated(): string {
  return screen.getAllByText(/^v=spf1/, { selector: "div" })[0].textContent ?? "";
}

describe("SpfRecordBuilder", () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it("starts from the published record: known services ticked, the rest kept", async () => {
    renderBuilder(builderData());
    expect(await screen.findByLabelText("Microsoft 365")).toBeChecked();
    expect(screen.getByLabelText("iCloud Mail")).not.toBeChecked();
    expect(generated()).toBe("v=spf1 include:spf.protection.outlook.com include:mail.example.net ~all");
    expect(screen.getByText("Same as published")).toBeInTheDocument();
  });

  it("adds a service, an address and the stricter qualifier", async () => {
    renderBuilder(builderData());
    fireEvent.click(await screen.findByLabelText("iCloud Mail"));
    fireEvent.change(screen.getByLabelText("IP address to add"), { target: { value: "192.0.2.10" } });
    fireEvent.click(screen.getByRole("button", { name: /Add address/ }));
    fireEvent.click(screen.getByLabelText(/hardfail: refuse it/));
    expect(generated()).toBe(
      "v=spf1 ip4:192.0.2.10 include:spf.protection.outlook.com include:icloud.com include:mail.example.net -all",
    );
  });

  it("drops an unknown include when unticked and refuses input that isn't a domain", async () => {
    renderBuilder(builderData());
    fireEvent.click(await screen.findByLabelText("include:mail.example.net"));
    expect(generated()).toBe("v=spf1 include:spf.protection.outlook.com ~all");
    fireEvent.change(screen.getByLabelText("Include to add"), { target: { value: "not a domain" } });
    fireEvent.click(screen.getByRole("button", { name: /Add include/ }));
    expect(screen.getByText(/isn't a domain name/)).toBeInTheDocument();
  });

  it("suggests approved senders SPF doesn't cover yet", async () => {
    renderBuilder(builderData({ suggestions: [{ provider_key: "smtp2go", sender_label: "SMTP2GO", messages: 12 }] }));
    fireEvent.click(await screen.findByRole("button", { name: /Add SMTP2GO/ }));
    expect(screen.getByLabelText("SMTP2GO")).toBeChecked();
    expect(screen.queryByRole("button", { name: /Add SMTP2GO/ })).not.toBeInTheDocument();
  });

  it("warns about two published records and about going over the lookup limit", async () => {
    renderBuilder(builderData({ current_record_count: 2 }), evaluation({ lookup_count: 12 }));
    expect(await screen.findByText(/This domain has 2 SPF records/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("12 of 10 DNS lookups")).toBeInTheDocument());
    expect(screen.getByText(/Over the limit of 10/)).toBeInTheDocument();
    expect(postMock).toHaveBeenCalledWith("/domains/domain-1/dns/spf-builder/evaluate", {
      record: "v=spf1 include:spf.protection.outlook.com include:mail.example.net ~all",
    });
  });
});
