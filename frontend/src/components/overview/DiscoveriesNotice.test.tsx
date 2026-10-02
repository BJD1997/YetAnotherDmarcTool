import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { Discoveries } from "../../api/overview";
import { makeTestQueryClient } from "../../test/render";
import DiscoveriesNotice from "./DiscoveriesNotice";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn() },
}));

const getMock = vi.mocked(api.get);

const DATA: Discoveries = {
  domains: [{ name: "other-brand.example", message_volume: 5, relationship: "apex" }],
  selectors: [{ domain_id: "d1", domain_name: "example.com", selectors: [{ selector: "s1", message_volume: 7 }] }],
};

function renderNotice(enabled = true) {
  return render(
    <QueryClientProvider client={makeTestQueryClient()}>
      <MemoryRouter>
        <DiscoveriesNotice enabled={enabled} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DiscoveriesNotice", () => {
  beforeEach(() => {
    getMock.mockReset();
    localStorage.clear();
  });

  it("lists unadded domains and unmonitored selectors with links to act on them", async () => {
    getMock.mockResolvedValue(DATA);
    renderNotice();

    expect(await screen.findByText("New in your reports")).toBeInTheDocument();
    expect(screen.getByText(/other-brand\.example/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add or dismiss" })).toHaveAttribute("href", "/settings/domains");
    expect(screen.getByRole("link", { name: "Add on the DNS tab" })).toHaveAttribute("href", "/domains/d1/dns");
  });

  it("stays dismissed until something new shows up", async () => {
    getMock.mockResolvedValue(DATA);
    const { unmount } = renderNotice();
    fireEvent.click(await screen.findByLabelText("Dismiss until something new shows up"));
    expect(screen.queryByText("New in your reports")).not.toBeInTheDocument();
    unmount();

    getMock.mockResolvedValue({ ...DATA, domains: [...DATA.domains, { name: "new.example", message_volume: 1, relationship: "apex" }] });
    renderNotice();
    expect(await screen.findByText("New in your reports")).toBeInTheDocument();
  });

  it("asks nothing for non-admins", () => {
    renderNotice(false);
    expect(getMock).not.toHaveBeenCalled();
  });
});
