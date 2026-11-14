import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import { renderWithAppProviders } from "../../test/render";
import DomainsTab from "./DomainsTab";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
vi.mock("../../auth/AuthContext", () => ({ useAuth: () => ({ user: { role: "org_admin" } }) }));
const getMock = vi.mocked(api.get);
const postMock = vi.mocked(api.post);

const detected = [
  { name: "hosting-test.nl", report_count: 3, message_volume: 40, relationship: "apex", suggested_parent_id: null, suggested_parent_name: null },
  {
    name: "web02.hosting-test.nl", report_count: 9, message_volume: 120, relationship: "subdomain_of_detected",
    suggested_parent_id: null, suggested_parent_name: "hosting-test.nl",
  },
];

beforeEach(() => {
  getMock.mockReset().mockImplementation(async (url: string) => (url === "/dmarc/detected-domains" ? detected : []));
  postMock.mockReset();
});

describe("Detected domains", () => {
  it("nests a detected subdomain under its detected root, which adds both", async () => {
    postMock.mockResolvedValueOnce({ added: ["hosting-test.nl", "web02.hosting-test.nl"], moved: [], root: "hosting-test.nl" });
    renderWithAppProviders(<DomainsTab />);

    expect(await screen.findByText(/verifies 1 subdomain/)).toBeInTheDocument();
    expect(screen.queryByText("web02.hosting-test.nl")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Add & verify" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/domains/detected", { name: "hosting-test.nl" }));
    expect(await screen.findByText(/Added hosting-test.nl with web02.hosting-test.nl/)).toHaveTextContent(
      "its subdomains are verified together with it",
    );
  });

  it("adds a nested subdomain on its own from under its root", async () => {
    postMock.mockResolvedValueOnce({});
    renderWithAppProviders(<DomainsTab />);

    fireEvent.click(await screen.findByRole("button", { name: "Show subdomains of hosting-test.nl" }));
    expect(screen.getByText("web02.hosting-test.nl")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add on its own" }));

    await waitFor(() =>
      expect(postMock).toHaveBeenCalledWith("/domains", { name: "web02.hosting-test.nl", parent_domain_id: null, mail_profile: "sends_mail" }),
    );
  });
});

describe("Pending domains", () => {
  it("lists a pending domain with its verification record and copy buttons", async () => {
    getMock.mockImplementation(async (url: string) => {
      if (url === "/domains")
        return [
          {
            id: "d1", name: "gtest.example", parent_domain_id: null, created_at: "2026-11-14T00:00:00Z",
            verification_status: "pending", verification_record_name: "_yadt-verify.gtest.example", verification_token: "yadt-verify=abc",
          },
        ];
      return [];
    });
    renderWithAppProviders(<DomainsTab />);

    expect(await screen.findByText("_yadt-verify.gtest.example")).toBeInTheDocument();
    expect(screen.getByText("yadt-verify=abc")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Copy/ })).toHaveLength(2);
    expect(screen.getByRole("button", { name: /Verify now/ })).toBeInTheDocument();
  });
});
