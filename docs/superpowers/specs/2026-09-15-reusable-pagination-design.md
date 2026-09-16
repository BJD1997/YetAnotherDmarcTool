# Reusable Cursor Pagination — Design

**Date:** 2026-09-15
**Status:** Approved in conversation; implementation plan to follow.

## Problem

Five views in the app render lists that grow without bound, and today each
handles that differently — or doesn't:

| View | File(s) | Current behavior |
|---|---|---|
| Sign-in log | `useSignInEvents.ts`, `SignInEventsSection.tsx`, `sign_in_events.py` (router + repo) | Cursor-paginated (`before_id`/`has_more`), hand-rolled |
| DMARC reports (by day) | `useDmarcReports.ts`, `DomainReports.tsx`, `dmarc_reports.py` | Cursor-paginated, hand-rolled separately from the above |
| TLS-RPT reports | `useTlsReports.ts`, `DomainTlsReports.tsx`, `dns_checks.py` | **Fetches the entire result set in one request**, groups client-side |
| Admin Organizations | `useAdmin.ts`, `AdminOrganizations.tsx`, `platform_admin.py` | **Fetches every organization in one request**, one large card per org, no search |
| Admin Job Runs | `useAdmin.ts`, `AdminJobRuns.tsx`, `platform_admin.py` | A "show last N" dropdown (20/50/100/200) — not true paging; rows past the selected cap are simply invisible |

The two working cases (sign-in log, DMARC-by-day) independently implement
the *identical* keyset-pagination technique: look up the anchor row
(`before_id`) by its ordering key, filter everything ordered before it,
sort, cap at `limit`. That duplication, plus the two genuinely broken cases
(TLS-RPT, Admin Organizations) and the one weak case (Admin Job Runs), are
what this design fixes.

Production data drives this too — TLS-RPT/DMARC report tables are designed
to hold years of history, and the platform now genuinely carries 200+
organizations (verified live via a production load-test seed on 2026-09-15,
see `backups/prod-loadtest-orgs-created-*.tsv`).

## Decision: cursor pagination, not page-number pagination

**Chosen approach:** one shared keyset/cursor pagination mechanism
(`before_id` + `limit` + `has_more`), reused by all five views, surfaced
in the UI as a "Load more" button (Admin Organizations additionally gets a
search box and summary bar on top of the same mechanism).

**Why not page numbers (`?page=3&page_size=50`, `OFFSET`-based):**
`OFFSET` pagination gets slower the deeper a query pages in, because the
database still has to walk and discard every row before the offset. DMARC
and TLS-RPT report tables are built to accumulate years of data — offset
paging would degrade exactly where it matters most. Keyset pagination's
cost doesn't grow with how deep you've paged. It would also mean rewriting
two endpoints that already work correctly, for no functional gain.

**Why cursor pagination still works for the admin views (not just
chronological logs):** Admin Organizations and Admin Job Runs aren't
naturally chronological-only, but keyset pagination doesn't require
chronological ordering — any column (or pair of columns) that's unique per
row and consistently ordered works as the cursor key. Admin Organizations
is ordered by `(name, id)` ascending (see below); Admin Job Runs keeps its
existing `started_at` descending order.

## Shared building blocks

### Backend: `app/services/pagination.py` (new)

One function that does what `list_sign_in_events` and
`list_report_records_by_day` already each do by hand:

```python
"""Shared keyset (cursor) pagination for list endpoints whose result sets
can grow without bound — see docs/superpowers/specs/2026-09-15-reusable-pagination-design.md.
Offset pagination is deliberately not used: its cost grows with how deep a
query pages, which is exactly wrong for tables built to hold years of data."""

from collections.abc import Sequence
from typing import TypeVar

from sqlalchemy import ColumnElement, Select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

Row = TypeVar("Row")


async def keyset_paginate(
    db: AsyncSession,
    query: Select,
    *,
    order_column: ColumnElement,
    id_column: ColumnElement,
    anchor_query: Select | None,
    limit: int,
) -> tuple[Sequence[Row], bool]:
    """Apply keyset pagination to `query`, already filtered/joined by the
    caller. `anchor_query` is a fully-scoped SELECT of (order_column,
    id_column) for the row identified by the caller's `before_id` — the
    caller builds it (not this function) so it can apply the same
    tenant/domain/org scoping the main query uses; pass None when there's
    no cursor yet (first page). Returns (rows, has_more); has_more is
    conservative — True whenever exactly `limit` rows come back, which can
    occasionally offer one extra empty "Load more" click at the true end of
    a result set (the same trade-off the two existing hand-rolled call
    sites already made, kept as-is rather than changed as a drive-by fix)."""
    if anchor_query is not None:
        anchor = (await db.execute(anchor_query)).first()
        if anchor is not None:
            query = query.where(tuple_(order_column, id_column) < anchor)

    query = query.order_by(order_column.desc(), id_column.desc()).limit(limit)
    rows = (await db.execute(query)).all()
    return rows, len(rows) == limit
```

`list_sign_in_events` and `list_report_records_by_day` are refactored to
call this instead of repeating the anchor-lookup-and-filter block inline —
each still builds its own base `query` (with its own tenant/domain scoping
and filters) and its own `anchor_query`, then hands both to
`keyset_paginate`. This is a behavior-preserving refactor: same SQL shape,
same response shape, existing tests for both must pass unchanged.

`list_tls_rpt_reports_for_domain`, `list_all_organizations`, and
`list_job_runs` are changed to accept `limit`/`before_id` and call the same
helper.

### Frontend: `useCursorPage` hook + `LoadMoreButton` component (new)

`frontend/src/hooks/useCursorPage.ts` wraps the `useInfiniteQuery`
boilerplate `useSignInEvents`/`useDmarcReportsByDay` each currently repeat:

```typescript
import { useInfiniteQuery } from "@tanstack/react-query";

interface CursorPage<Item> {
  items: Item[];
  has_more: boolean;
}

export function useCursorPage<Item>(
  queryKey: readonly unknown[],
  fetchPage: (cursor: string | undefined) => Promise<CursorPage<Item>>,
  getCursor: (item: Item) => string,
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.has_more && lastPage.items.length > 0 ? getCursor(lastPage.items[lastPage.items.length - 1]) : undefined,
    enabled: options?.enabled,
  });
}
```

Each resource's existing endpoint response shape is preserved (e.g.
sign-in events still returns `{events: [...], has_more}`, not a renamed
`items` field) — call sites pass a small adapter as `fetchPage` that maps
their endpoint's actual field name to `{items, has_more}` for the hook,
so no API response shape changes for the two already-working endpoints.

`frontend/src/components/shared/LoadMoreButton.tsx` replaces the
hand-written button block in `SignInEventsSection.tsx` (and the equivalent
in `DomainReports.tsx`):

```tsx
interface LoadMoreButtonProps {
  hasNextPage: boolean | undefined;
  isFetchingNextPage: boolean;
  onClick: () => void;
}

export function LoadMoreButton({ hasNextPage, isFetchingNextPage, onClick }: LoadMoreButtonProps) {
  if (!hasNextPage) return null;
  return (
    <button className="btn btn--secondary" style={{ marginTop: "0.75rem" }} onClick={onClick} disabled={isFetchingNextPage}>
      {isFetchingNextPage ? "Loading…" : "Load more"}
    </button>
  );
}
```

Row/card rendering (table columns, org cards, job-run rows) is **not**
shared — each view's rows look nothing alike, so only the fetch/paging
mechanics and the load-more control are extracted.

## Per-view changes

### TLS-RPT reports

- `GET /domains/{id}/dmarc/tls-rpt/reports` gains `limit` (default 50,
  same bounds as sign-in events: 1-200) and `before_id`. Cursor key:
  `(date_range_begin, id)` descending — matches the existing "newest
  first" ordering and mirrors DMARC-by-day's own cursor shape.
- **Known trade-off, kept as-is:** `result_type` filtering happens in
  Python after the SQL fetch (`failure_details` is a JSONB array; see the
  existing comment in `_fetch_tls_rpt_rows` — not worth a
  `jsonb_array_elements` query at this data volume). With pagination, a
  page can return fewer than `limit` visible rows when `result_type` is
  set and thins out that page's SQL rows. The cursor still advances
  correctly (from the last SQL row fetched, not the last Python-filtered
  row), so "Load more" remains correct — just occasionally returns a
  smaller visible batch than usual under that filter. Redesigning the
  JSONB filtering strategy itself is out of scope here.
- `DomainTlsReports.tsx`: the day-grouped view switches from
  `useTlsReportRows` (plain `useQuery`, fetch-all) to `useCursorPage` +
  `LoadMoreButton`, grouping only the rows fetched so far. The by-sender
  view (`BySenderTable`) is a distinct aggregate endpoint, already small,
  and is unaffected.

### Admin Organizations

- `GET /admin/organizations` gains `limit` (default 50), `before_id`, and
  `search` (case-insensitive substring match on `name`, mirrors the
  `org_name` ILIKE pattern already used in TLS-RPT filtering). Cursor key:
  `(name, id)` ascending — alphabetical, matching how an admin scans for a
  specific org by name (rather than newest-first, which would bury most
  orgs behind whichever were created most recently — exactly the ordering
  that would make the 200 just-seeded load-test orgs dominate page one).
- Response adds a `total` count (`SELECT count(*)`, same `search` filter
  applied, no pagination) and a summary rollup (active/suspended split,
  count with job errors in the last 7 days) for the glanceable bar —
  computed once per request, not per page item.
- `org_aggregates` (domain counts, last-report time, 7-day job errors)
  already takes a list of org ids and batches its lookups — called with
  just the current page's ids instead of every org's, so it stays cheap as
  the org count grows.
- `AdminOrganizations.tsx`: adds the search input and summary bar from the
  earlier UI design; replaces the always-expanded `OrgCard` list with a
  compact row per org (name, status badge, domain count, last-report
  freshness, error badge) that expands in place on click into the existing
  `OrgCard` detail (tenant ID, mailbox, delete, create-user) — no new
  route. Paired with `useCursorPage` + `LoadMoreButton`; typing in search
  resets the query (new `queryKey`, cursor restarts).

### Admin Job Runs

- `GET /admin/job-runs` changes from `limit` (bare cap) to `limit` +
  `before_id`, cursor key `(started_at, id)` descending — same ordering
  it already uses, just paged instead of hard-capped.
- `AdminJobRuns.tsx`: the "Show last N" dropdown is removed; replaced with
  `useCursorPage` + `LoadMoreButton`. The existing summary widget
  (`useAdminJobRunsSummary`) is untouched — it's a separate aggregate
  endpoint, not part of the list itself.

### Sign-in log / DMARC reports (by day)

Refactor only — call the new shared `keyset_paginate` helper and
`useCursorPage`/`LoadMoreButton` instead of their hand-rolled equivalents.
No change to their API response shapes, query params, or UI behavior.
Existing tests for both must pass unchanged; this is the regression
safety net for the refactor.

## Explicitly out of scope

- Mailbox sync history (Settings → Mailbox): fixed `limit=15`, no
  load-more — a small "recent activity" widget by design, not a growing
  log.
- Sender reviews (per-domain service list): bounded by distinct senders
  seen, not by time.
- Domains/users/DKIM-selectors per org: bounded by org size (typically a
  handful to a few dozen rows), not the class of problem this design
  addresses.

## Testing

- Backend: new unit tests for `keyset_paginate` directly (empty result,
  single page, multi-page walk, `before_id` pointing at a row that no
  longer exists). Existing sign-in-events/DMARC-by-day tests must pass
  unchanged after the refactor. New tests for TLS-RPT reports, Admin
  Organizations (including `search`), and Admin Job Runs pagination
  (multi-page walk, `has_more` correctness, cursor stability against
  concurrently-inserted rows).
- Frontend: `useCursorPage` unit-tested in isolation (mock `fetchPage`,
  verify cursor extraction and `hasNextPage` wiring). Existing
  `SignInEventsSection`/`DomainReports` tests updated for the refactor,
  not deleted. New tests for the Admin Organizations compact-row/expand
  interaction and the TLS-RPT/Admin Job Runs Load-more flow.
