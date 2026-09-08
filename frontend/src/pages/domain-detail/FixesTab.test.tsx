import { render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Domain } from "../../api/types";
import { useActionQueue } from "../../hooks/useDomainInsights";
import FixesTab from "./FixesTab";

vi.mock("../../hooks/useDomainInsights", () => ({ useActionQueue: vi.fn() }));

const actionQueueMock = vi.mocked(useActionQueue);
const domain = { id: "domain-1", name: "example.com" } as Domain;

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/domains/domain-1/fixes"]}>
      <Routes>
        <Route path="/domains/:domainId" element={<Outlet context={domain} />}>
          <Route path="fixes" element={<FixesTab />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Fixes page", () => {
  it("shows the resource-hook loading state", () => {
    actionQueueMock.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useActionQueue>);
    renderPage();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("shows the clean state when the action queue is empty", () => {
    actionQueueMock.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useActionQueue>);
    renderPage();
    expect(screen.getByText("Nothing needs attention right now.")).toBeInTheDocument();
  });
});
