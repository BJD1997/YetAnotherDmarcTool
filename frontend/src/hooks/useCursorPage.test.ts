import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { makeQueryClientWrapper } from "../test/render";
import { useCursorPage } from "./useCursorPage";

interface Page { items: string[]; has_more: boolean }

describe("useCursorPage", () => {
  it("fetches the first page and exposes hasNextPage from has_more", async () => {
    const fetchPage = vi.fn(async (_cursor: string | undefined): Promise<Page> => ({
      items: ["a", "b"],
      has_more: true,
    }));
    const getNextCursor = (last: Page) => (last.has_more ? last.items[last.items.length - 1] : undefined);

    const { result } = renderHook(() => useCursorPage(["test-key"], fetchPage, getNextCursor), {
      wrapper: makeQueryClientWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(fetchPage).toHaveBeenCalledWith(undefined);
    expect(result.current.data?.pages).toEqual([{ items: ["a", "b"], has_more: true }]);
    expect(result.current.hasNextPage).toBe(true);
  });

  it("passes the extracted cursor into fetchPage on fetchNextPage", async () => {
    const fetchPage = vi.fn(async (cursor: string | undefined): Promise<Page> =>
      cursor === undefined ? { items: ["a", "b"], has_more: true } : { items: ["c"], has_more: false },
    );
    const getNextCursor = (last: Page) => (last.has_more ? last.items[last.items.length - 1] : undefined);

    const { result } = renderHook(() => useCursorPage(["test-key-2"], fetchPage, getNextCursor), {
      wrapper: makeQueryClientWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    result.current.fetchNextPage();
    await waitFor(() => expect(result.current.data?.pages.length).toBe(2));

    expect(fetchPage).toHaveBeenLastCalledWith("b");
    expect(result.current.hasNextPage).toBe(false);
  });
});
