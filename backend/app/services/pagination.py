"""Shared keyset (cursor) pagination for list endpoints whose result sets
can grow without bound — see
docs/superpowers/specs/2026-09-15-reusable-pagination-design.md. Offset
pagination is deliberately not used: its cost grows with how deep a query
pages, which is exactly wrong for tables built to hold years of data."""

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
    scalar: bool = True,
    descending: bool = True,
) -> tuple[Sequence[Row], bool]:
    """Apply keyset pagination to `query`, already filtered/joined by the
    caller. `anchor_query` is a fully-scoped SELECT of (order_column,
    id_column) for the row identified by the caller's `before_id` — the
    caller builds it (not this function) so it can apply the same
    tenant/domain/org scoping the main query uses; pass None when there's
    no cursor yet (first page), or when `before_id` resolved to no row
    (falls back to the first page, same as the two hand-rolled call sites
    this replaces).

    `scalar=True` (default) unwraps a single-entity select (e.g.
    `select(Organization)`) via `.scalars().all()`. Pass `scalar=False` for
    a multi-column projection (e.g. a join selecting individual columns),
    which gets raw `Row` tuples via `.all()` instead — pick whichever
    matches how the caller built `query`.

    `descending=True` (default) orders and pages newest/highest-first,
    appropriate for chronological logs. Pass `descending=False` for
    ascending order (e.g. alphabetical) — this flips both the ORDER BY
    and the keyset comparison direction, not just the sort. Under
    `descending=False` (currently only Admin Organizations, paging
    alphabetically ascending), the row identified by `anchor_query` is the
    one *after* which pagination continues — i.e. the last row of the
    previous page — not "before" in the row-comparison sense the
    `before_id`/`anchor_query` naming otherwise suggests (that naming comes
    from the descending case, where the next page's rows do sort before
    the anchor).

    Returns (rows, has_more); has_more is conservative — True whenever
    exactly `limit` rows come back, which can occasionally offer one extra
    empty "Load more" click at the true end of a result set (the same
    trade-off the two existing hand-rolled call sites already made, kept
    as-is rather than changed as a drive-by fix)."""
    if anchor_query is not None:
        anchor = (await db.execute(anchor_query)).first()
        if anchor is not None:
            comparison = tuple_(order_column, id_column) < anchor if descending else tuple_(order_column, id_column) > anchor
            query = query.where(comparison)

    order = (order_column.desc(), id_column.desc()) if descending else (order_column.asc(), id_column.asc())
    query = query.order_by(*order).limit(limit)

    result = await db.execute(query)
    rows = result.scalars().all() if scalar else result.all()
    return rows, len(rows) == limit
