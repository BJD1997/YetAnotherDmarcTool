# Backend/Frontend Cohesion Refactor — Design

**Date:** 2026-09-05
**Status:** Approved for planning
**Target release:** v0.1.4 (stable, from `master`)

## Motivation

YetAnotherDmarcTool has grown through steady, incremental feature delivery (confirmed via `git log` — no churn/reverts), but with no architectural boundary to absorb new features, each one has added more inline code to already-large files. The developer's own assessment: the codebase "needs some cohesion — it's falling apart with every new feature added."

An investigation of the current codebase (`backend/app`, `frontend/src`) found concrete, load-bearing evidence of this:

1. **No schema layer.** `backend/app/schemas/` is empty. All 21 Pydantic request/response models are defined inline inside router files. This is the direct cause of `backend/app/routers/dmarc_reports.py` being 1197 lines.
2. **No repository/query boundary.** Routers issue direct `select()` calls (27 in `dmarc_reports.py` alone) and services independently issue their own queries too — there is no single place responsible for database access.
3. **Duplication that follows directly from #2.** `_get_owned_domain(db, domain_id, organization_id)` is copy-pasted byte-for-byte in three router files (`dns_checks.py`, `dmarc_reports.py`, `selectors.py`), called 23 times combined.
4. **No frontend data-fetching layer.** No `frontend/src/hooks/` directory exists; every page inlines its own `react-query` calls directly (up to 12 call sites in `Onboarding.tsx`).
5. **Zero test coverage where it's needed most.** No HTTP/integration tests exist for any of the 14 router files (~4000 lines combined), and no frontend tests exist at all. The least-layered code has no test net, which is exactly why changes there feel fragile.
6. **Secondary organizational drift.** `backend/app/services/` mixes flat single-purpose files with feature subpackages, with no stated rule for when a service "graduates" to a subpackage. `frontend/src/pages/` shows the same pattern (two ad hoc nested groupings — `domain-detail/`, `settings/` — introduced opportunistically).
7. **`main.py` god file.** 243 lines, hosting 5 hand-written middlewares that carry real business logic (demo-read-only enforcement, MTA-STS host routing, CSRF, security headers, SPA path-traversal guard) inline rather than as separate modules.

## Goals

- Establish a consistent, documented set of structural conventions for the backend and frontend that new features can follow without reinventing organization each time.
- Eliminate the concrete duplication and oversized-file problems identified above, across the whole codebase (not just the worst offenders).
- Add a real test net (backend HTTP-level integration tests + frontend test tooling and tests) so future changes can be verified rather than trusted by inspection.
- Do all of this without compromising the horizontal-scale-out design already built on the `v0.1.4-beta` branch (stateless API, Postgres-backed work queue, N-replica-safe by construction) — this refactor happens on `master` first, and `v0.1.4-beta` will be rebased onto it afterward.

## Non-goals

- Building or changing anything in the scale-out work itself (Postgres job queue, leader election, Azure deploy, IMAP ingestion) — that remains scoped to `v0.1.4-beta` / the future v0.1.5 and Phase 3 work, tracked separately.
- Introducing new product features or changing any user-facing behavior. This is a pure internal-structure refactor; existing behavior must be preserved (which is exactly what the new test suite is for).
- Reorganizing `backend/app/services/` or `frontend/src/pages/` wholesale. Only the concrete drift identified above gets addressed, plus a documented rule for future consistency — not directory churn for its own sake.

## Design

### 1. Backend schema layer

New `backend/app/schemas/`, one module per resource area, mirroring router names (`schemas/dmarc_reports.py`, `schemas/auth.py`, `schemas/domains.py`, `schemas/users.py`, `schemas/platform_admin.py`, `schemas/admin_updates.py`, `schemas/mailbox_connections.py`, `schemas/organizations.py`, `schemas/selectors.py`, etc.). All 21 existing inline Pydantic `BaseModel`s move out of their router files into the corresponding schema module. Routers import from `schemas` and keep only route wiring plus calls into the repository layer.

### 2. Backend repository/query layer

New `backend/app/repositories/`, one file per model/domain area, mirroring `backend/app/models/` (`repositories/domains.py`, `repositories/reports.py`, `repositories/users.py`, etc.). Every `select()`/database query currently inline in routers or duplicated across services moves here. The triplicated `_get_owned_domain()` collapses into a single `repositories/domains.py::get_owned_domain()`, called from every site that currently copy-pastes it. Routers and services call repository functions; neither issues raw queries directly anymore. Two categories of raw SQL are a deliberate, permanent exception to this: `db/rls.py`'s session-context `SET LOCAL`/`set_config` calls, and the two workers' `pg_try_advisory_lock`/`pg_advisory_unlock` calls on their dedicated physical connections — neither is a data query, so neither belongs in the repository layer.

**Statelessness constraint (hard requirement):** every repository function takes `db: AsyncSession` as an argument and returns data — no module-level caches, singletons, or other process-local state. This preserves the horizontal-scale-out property (stateless API, safe under N replicas) that the `v0.1.4-beta` branch already established and that `master` must not regress.

**Forward-compatibility note:** the job-queue's `FOR UPDATE SKIP LOCKED` claim queries currently live inline in `services/jobs/` on the `v0.1.4-beta` branch (not present on `master` today). `repositories/` is shaped so there's an obvious home for those (e.g. `repositories/jobs.py`) once that branch is rebased onto the new `master` — not moved now, but planned for so the rebase lands the queue code into the new convention instead of leaving it orphaned.

### 3. `main.py` middleware split

The 5 middlewares currently inline in `main.py` (demo-read-only enforcement, MTA-STS host routing, CSRF enforcement, security headers, SPA path-traversal guard) move to their own modules under the existing `backend/app/middleware/` directory. `main.py` becomes app setup and wiring only. Each middleware must remain stateless per-request — the split relocates code, it does not introduce new in-memory state that would break correctness across replicas (this was exactly the mistake the pre-scale-out rate limiter made, later fixed with a pluggable Postgres backend on the beta branch).

### 4. Frontend hooks layer

New `frontend/src/hooks/`, one hook per resource, mirroring the existing `api/` layer (`hooks/useDomains.ts`, `hooks/useDmarcReports.ts`, `hooks/useOnboarding.ts`, etc.). Each hook wraps the `useQuery`/`useMutation` calls that currently live inline in page components, exposing a `{ data, isLoading, error, mutate }`-style interface. All 21 pages migrate to call hooks instead of `useQuery`/`useMutation` directly.

### 5. Backend services organization rule

Codify a rule for `backend/app/services/`, which currently mixes flat single-purpose files (`dmarc_analytics.py`, `dmarc_narrative.py`, `update_check.py`, `updater_client.py`) with 11 feature subpackages (`auth/`, `dns_checks/`, `jobs/`, etc.) with no stated reason for the split: a service becomes a subpackage once it needs 2+ files to express (matches the existing subpackage examples); a service that's genuinely one file stays flat at `services/` root. Under this rule, the current flat files stay flat as-is — the drift was undocumented convention, not a wrong outcome, so no files need to move. Document this rule alongside the code (e.g. a short note in `backend/app/services/README.md` or the top-level project docs) so it's a checklist for future features instead of tribal knowledge.

### 6. Pages organization rule

Codify the pattern already hinted at by the existing `domain-detail/` and `settings/` groupings: a feature gets its own subdirectory under `pages/` once it has 2+ related page files; a single standalone page stays flat at `pages/` root. Audit current flat pages against this rule as part of the work (e.g. the `Admin*` pages likely belong grouped) rather than moving files without a concrete reason. Document alongside the services rule (section 5) in the same place.

### 7. Shared components

`components/shared/` currently holds a single 23-line file. As the hooks migration removes duplicated data-fetching logic from pages, any genuinely-shared UI pieces that surface during that work move here instead of staying duplicated per-page. This is opportunistic, not a separate sweep.

### 8. Testing

**Backend:** HTTP-level integration tests covering all 14 routers, organized under `backend/tests/routers/test_<name>.py`, using the existing `httpx`/FastAPI test-client pattern. These are written *before* the schema/repository extraction (see Sequencing) so they characterize current behavior and catch regressions during the restructuring.

**Frontend:** test tooling does not exist today and needs to be introduced from scratch (Vitest + React Testing Library, the standard pairing for a Vite + React app). New dependencies are pinned to exact latest-stable versions per existing project convention (see `dmarcwatch-dependency-pinning` memory). Tests cover the migrated hooks and key pages.

## Sequencing

Lowest-risk to highest-risk, so that risk is only taken on once a safety net exists:

1. **Backend integration tests** against current (pre-refactor) code — establishes the regression check.
2. **Schema layer extraction** — mechanical, low risk (relocating definitions, not logic).
3. **Repository layer extraction + dedup** — the highest-value, highest-risk step; step 1's tests are what make this safe.
4. **`main.py` middleware split** — mechanical, low risk.
5. **Frontend hooks layer + frontend test tooling** — same pattern as backend: tests in place, then migrate.
6. **Full verification pass** — build gate (`docker compose build api worker migrate`), live-HTTP checks, per existing verification discipline (see `dmarcwatch-verification-discipline` memory).

## Branch / release plan

- Work happens on a new branch cut from `master` (currently `v0.1.3` stable), in an isolated worktree — not on the currently-checked-out `v0.1.4-beta` branch, and not directly on `master`.
- On completion and full verification, merge to `master` and tag **`v0.1.4`** as the new stable release.
- Afterward, rebase `v0.1.4-beta` onto the new `master`. This is expected to produce real conflicts (it touches `main.py` and `services/jobs/`, both restructured here) — the scale-out code gets adapted onto the new schema/repository/middleware conventions as part of that rebase. That branch's eventual stable release becomes **`v0.1.5`** (renumbered from its original `v0.1.4-beta` target, since `v0.1.4` is now taken by this refactor).

## Risks

- **Scope size.** This touches most of the backend router/service code (~4000 lines) and most frontend pages (~4600 lines). It will take multiple work sessions, not one. Sequencing (above) exists specifically to keep partial progress safe at every step.
- **Beta-branch rebase conflicts.** Deferred to when the rebase actually happens; not solved by this design. Flagged here so it isn't a surprise later.
- **New frontend test tooling.** Introducing Vitest/RTL from zero means the first tests double as tooling validation — expect some setup friction before the first frontend test actually runs green.

## Out of scope (tracked elsewhere)

- Azure deploy infra (Bicep, ACA, Key Vault, VNet) — `v0.1.4-beta` Phase 2, see `dmarcwatch-project` memory.
- IMAP / Google Workspace ingestion — Phase 3, not yet built.
- Remaining firewall hardening backlog (NPM real-IP to Cloudflare ranges, origin-port restriction) — see `dmarcwatch-security-hardening` memory.
