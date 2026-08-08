# Contributing

Issues and PRs are welcome — this started as a personal hobby project (see
the README's opening), so there's no formal process yet, just a few things
that make review faster.

## Local development

See the README's [Getting started](README.md#getting-started) section to
run the whole stack via Docker Compose. For iterating on just one side:

- **Backend**: `uvicorn app.main:app --reload` from `backend/`, pointed at
  a `DATABASE_URL` for a running `db`/`resolver` (the `docker compose`
  stack works fine for this — just run the backend outside its container).
- **Frontend**: `npm run dev` from `frontend/` — the Vite dev server proxies
  `/api` to `localhost:8000` (see `vite.config.ts`), so run the backend
  first.

## Before opening a PR

- `docker compose build api worker` should succeed (`tsc -b` runs as part
  of the frontend build stage, so this also catches type errors).
- `cd backend && pip install -r requirements-dev.txt && pytest` should pass.
  CI runs this (and the frontend build) automatically on every PR.
- New Alembic migrations go in `backend/alembic/versions/`, hand-written
  rather than autogenerate-only — include the row-level-security policy if
  the migration adds a new tenant-owned table (see any existing migration
  for the pattern).
- The test suite (see the README's Development section) covers the pure
  logic — the RFC-driven checkers, domain matching, rating computation —
  not yet anything that needs a real database. If you're touching one of
  the covered modules, add/update a test alongside the change rather than
  relying only on live verification; if you're touching something the
  suite doesn't reach yet, live verification against a real running
  instance is still the expectation, same as before.

## Code style

No linter/formatter is enforced yet, but the existing code leans on a few
conventions worth matching:

- Comments explain *why*, not *what* — skip a comment if the code already
  says what it does.
- Prefer reusing an existing pattern over inventing a new one for the same
  kind of problem (e.g. how optional features are gated by an unset config
  value rather than a feature flag).
- Keep changes scoped to what's actually needed — this codebase avoids
  speculative abstraction for cases that don't exist yet.
