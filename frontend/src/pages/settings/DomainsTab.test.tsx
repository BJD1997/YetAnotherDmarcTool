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
  it("adds a detected subdomain together with its detected root", async () => {
    postMock.mockResolvedValueOnce({ added: ["hosting-test.nl", "web02.hosting-test.nl"], moved: [], root: "hosting-test.nl" });
    renderWithAppProviders(<DomainsTab />);

    fireEvent.click(await screen.findByRole("button", { name: "Add with hosting-test.nl" }));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith("/domains/detected", { name: "web02.hosting-test.nl" }));
    expect(await screen.findByText(/Added hosting-test.nl with web02.hosting-test.nl/)).toHaveTextContent(
      "its subdomains are verified together with it",
    );
  });

  it("keeps Add & verify for a root domain", async () => {
    renderWithAppProviders(<DomainsTab />);

    expect(await screen.findAllByRole("button", { name: "Add & verify" })).toHaveLength(1);
  });
});
