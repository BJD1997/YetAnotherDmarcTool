import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Domains from "./Domains";
import { useDomains, useRankedDomains } from "../hooks/useDomains";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { role: "org_admin" } }),
}));
vi.mock("../hooks/useDomains", () => ({
  useDomains: vi.fn(),
  useRankedDomains: vi.fn(),
  useVerifyDomain: vi.fn(),
  useDeleteDomain: vi.fn(),
  useUpdateDomain: vi.fn(),
}));

const domainsMock = vi.mocked(useDomains);
const rankedMock = vi.mocked(useRankedDomains);

describe("Domains page", () => {
  beforeEach(() => {
    rankedMock.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useRankedDomains>);
  });

  it("shows the empty state and management path when no domains exist", () => {
    domainsMock.mockReturnValue({ data: [], isLoading: false } as unknown as ReturnType<typeof useDomains>);
    render(<MemoryRouter><Domains /></MemoryRouter>);

    expect(screen.getByText(/No domains yet/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Add domain/i })).not.toHaveLength(0);
  });

  it("preserves the loading state supplied by the resource hook", () => {
    domainsMock.mockReturnValue({ data: undefined, isLoading: true } as ReturnType<typeof useDomains>);
    render(<MemoryRouter><Domains /></MemoryRouter>);

    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});
