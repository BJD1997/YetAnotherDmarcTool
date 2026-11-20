import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { queryKeys } from "../hooks/queryKeys";
import UpdatedBanner from "./UpdatedBanner";

vi.mock("../api/client", () => ({ api: { get: vi.fn() }, ApiError: class ApiError extends Error {} }));

describe("UpdatedBanner", () => {
  it("stays hidden until the server's version changes, then offers a reload", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.get).mockResolvedValue({ version: "v0.3.0-beta13" });
    render(
      <QueryClientProvider client={client}>
        <UpdatedBanner />
      </QueryClientProvider>,
    );
    // The page loaded with beta13.
    await waitFor(() => expect(client.getQueryData(queryKeys.health)).toEqual({ version: "v0.3.0-beta13" }));
    await act(async () => {});
    expect(screen.queryByText(/was updated/)).not.toBeInTheDocument();

    vi.mocked(api.get).mockResolvedValue({ version: "v0.3.0-beta14" });
    await act(() => client.refetchQueries({ queryKey: queryKeys.health }));

    await waitFor(() => expect(document.body.textContent).toContain("was updated to"));
    expect(screen.getByText("v0.3.0-beta14")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reload/ })).toBeInTheDocument();
  });
});
