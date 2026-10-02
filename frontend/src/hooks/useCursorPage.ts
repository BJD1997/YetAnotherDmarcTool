import { useInfiniteQuery } from "@tanstack/react-query";

/** Shared cursor-pagination wiring for list views whose result sets can
 * grow without bound — see
 * docs/superpowers/specs/2026-09-15-reusable-pagination-design.md. Each
 * resource keeps its own page response shape (e.g. {events, has_more},
 * {days, has_more}) — pass `fetchPage` to inject the cursor into that
 * resource's own URL/params, and `getNextCursor` to pull the next
 * `before_id` out of that resource's own last page (or return undefined
 * when there's no more). */
export function useCursorPage<Page>(
  queryKey: readonly unknown[],
  fetchPage: (cursor: string | undefined) => Promise<Page>,
  getNextCursor: (lastPage: Page) => string | undefined,
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: getNextCursor,
    enabled: options?.enabled,
  });
}
