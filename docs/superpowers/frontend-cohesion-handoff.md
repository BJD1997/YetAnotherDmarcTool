# Frontend cohesion refactor — progress and handoff

**Branch:** `refactor/frontend-cohesion`
**Base:** `master` at `v0.1.4-rc5` (`69e7d6b`)
**Started:** 2026-09-08

## Objective

Complete the frontend half of the cohesion-refactor design without changing
product behaviour: add a regression test net, centralize repeated React Query
configuration behind resource hooks, and split only the page components whose
mixed responsibilities make changes risky.

This work is independent of the Azure scale-out port. The branch is kept in
small commits so either effort can proceed or be handed over separately.

## Working rules

- Preserve visible behaviour and existing API contracts.
- Add characterization tests before migrating a flow.
- Keep query keys and cache invalidation in the hook/resource layer.
- Do not create one-line hooks that have only one caller and add no policy.
- Split components by responsibility, never by an arbitrary line limit.
- Use exact dependency versions. The production image remains on Node 20, so
  test dependencies must support Node 20.
- After every chunk: run its focused tests, the full frontend suite, and the
  production frontend build; record the result here.

## Chunk ledger

### Commit checkpoints

- `c6c8778` — test infrastructure and baseline coverage (Chunks 0–1)
- `adb71e0` — canonical query keys and initial resource hooks (Chunk 2)
- `a5475ab` — shell, overview, domains, and settings entry points (Chunk 3A)
- `3fe62f6` — remaining shared-resource migrations and invalidations (Chunk 3B)
- `4f8fa67` — page organization and shared clipboard interaction (Chunk 4)
- `8606db1` — ignore generated Vitest configuration artifacts (final audit)

### Chunk 0 — branch, inventory, and handoff log

**Status:** complete

- Created `refactor/frontend-cohesion` from clean `master`/`v0.1.4-rc5`.
- Confirmed there was no frontend test runner or frontend test file.
- Confirmed repeated inline React Query configuration across pages/components.
- Identified the largest mixed-responsibility screens: `DnsChecksTab`,
  `Onboarding`, `DomainReports`, `AdminOrganizations`, and `SenderInventory`.
- Selected Vitest 4.x rather than 5.x: Vitest 5 requires Node 22.12+, while the
  repository's Docker frontend build uses Node 20. Vitest 4 supports Node 20.
- Selected `@testing-library/jest-dom` 6.9.1: 6.10.0 and 7.x require Node 22,
  while 6.9.1 is the newest release compatible with the project's Node 20.

**Verification:** branch created from a clean worktree; inventory recorded.

### Chunk 1 — test infrastructure and baseline characterization tests

**Status:** complete

- Added Vitest 4.1.11, jsdom 26.1.0, React Testing Library, jest-dom, and
  user-event with exact versions compatible with the Node 20 build image.
- Converted all existing ranged frontend dependencies to the exact versions
  already resolved in the previous lockfile; this removes manifest/lockfile
  policy drift without changing the installed production dependency versions.
- Added `test` and `test:watch` scripts, isolated Vitest configuration, global
  DOM matcher/cleanup setup, and a reusable router/React Query render helper.
- Added baseline tests for the central API client (cookies, CSRF/JSON headers,
  204 responses, structured errors), DMARC/TLS filter serialization, and all
  branches of the no-data operational recommendation.
- Added the frontend suite to GitHub Actions before the production build.

**Verification (Node 20 container):** 3 test files, 12 tests passed; TypeScript
and Vite production build passed; npm audit reported 0 vulnerabilities.

### Chunk 2 — canonical query keys and shared resource hooks

**Status:** complete

- Added canonical query-key builders for domains, organization, mailbox,
  onboarding, users, admin organizations, and admin updates.
- Added shared read hooks for domains, current organization, current mailbox
  connection, and onboarding status—the four most widely repeated resources.
- Added a reusable Query Client wrapper for hook tests.
- Added hook tests proving canonical endpoints, successful result propagation,
  and parameterized key structure.

**Verification (Node 20 container):** 4 test files, 17 tests passed; TypeScript
and Vite production build passed.

### Chunk 3 — page/component hook migrations

**Status:** complete

#### Chunk 3A — application shell and overview/domain entry points

- Migrated `Shell`, `SettingsLayout`, `Overview`, `Domains`, `CommandBar`, and
  `MailboxHealthWidget` from repeated inline queries to the shared resource
  hooks.
- Extended organization/mailbox hooks with a deliberately narrow options
  surface (`enabled`, `retry`, `refetchInterval`) needed to preserve existing
  caller behaviour; arbitrary query policy remains owned by the hooks.
- Left unrelated, single-purpose queries in place for later resource-specific
  hooks rather than mixing migrations in this commit.

**Verification (Node 20 container):** 4 test files, 17 tests passed; TypeScript
and Vite production build passed.

The remaining migrations from this checkpoint were completed in Chunk 3B below.

#### Chunk 3B — remaining shared resources and cache invalidation

- Migrated Onboarding, Team, add-domain and mailbox settings, both reporting
  policy builders, DNS checks, domain detail, admin organizations/job runs/
  updates, and auth-context consumers onto shared resource hooks or canonical
  query keys.
- Added hooks for users, domain detail, admin organizations, and admin update
  status. Shared admin response shapes now have one canonical type definition.
- Expanded the narrowly allowed hook options to preserve the onboarding domain
  polling and mailbox post-save polling exactly as they behaved before.
- Replaced raw invalidation arrays for shared resources with `queryKeys`, so a
  future key change cannot leave mutations refreshing a different cache entry.
- Deliberately left feature-local, parameterized queries (individual report
  views, charts, filters, and policy-builder payloads) beside their only current
  consumer. Wrapping those one-for-one would add files without centralizing
  shared policy; they can move when a second consumer exists or during the
  focused component splits in Chunk 4.
- Extended resource-hook tests to cover users, admin organizations, admin
  updates, and parameterized domain detail.

**Verification (Node 20 container):** 4 test files, 21 tests passed; TypeScript
and Vite production build passed. A targeted sweep found no remaining raw keys
or invalidations for the migrated shared resources.

**Next:** Chunk 4—organize the page boundary and extract only responsibilities
that are genuinely shared.

### Chunk 4 — focused component and page organization

**Status:** complete

- Grouped the four platform-administration routes under `pages/admin/` and the
  two report routes under the existing `pages/domain-detail/` feature folder.
  Routing paths and visible behaviour are unchanged; only source ownership and
  imports moved.
- Added `pages/README.md` with the durable organization rule: standalone routes
  stay flat, related route families get a feature folder, and reusable UI goes
  under `components/`.
- Audited the previously identified large screens. `Onboarding`,
  `DomainReports`, `DnsChecksTab`, `AdminOrganizations`, and `SenderInventory`
  already divide their screen sections into named local components/functions.
  Moving every local section to a separate file would increase navigation cost
  without changing ownership, state boundaries, or testability, so no
  line-count-only split was made.
- Found one genuinely repeated interaction during that audit: seven components
  independently implemented clipboard writes plus a two-second success timer.
  Added the tested `useClipboardFeedback` hook and migrated every copy site,
  including the MTA-STS builder's two independent copy buttons.
- The shared hook also fixes two quiet lifecycle risks in the duplicated code:
  it cancels the timer when its component unmounts and restarts the timer when
  the user copies again.

**Verification (Node 20 container):** 5 test files, 22 tests passed; TypeScript
and Vite production build passed. A source sweep found no direct clipboard
writes remaining outside the shared hook and its test.

**Next:** Chunk 5—perform the final branch-wide review, run all appropriate
frontend and repository checks, and finish the Claude handoff/rebase guidance.

### Chunk 5 — final verification and handoff

**Status:** complete

- Reviewed the complete `master...refactor/frontend-cohesion` diff for scope,
  route/import moves, query-key usage, generated files, and whitespace errors.
- Removed accidentally tracked JavaScript/declaration output generated from
  `vitest.config.ts` and added it to `.gitignore`, matching the existing rule
  for generated `vite.config` output.
- Re-ran the complete frontend suite and production build in the repository's
  Node 20 environment.
- Ran the backend suite as a regression check even though this branch changes
  no backend application code. All 144 locally runnable tests passed; 172
  database/configuration integration cases skipped because the local invocation
  did not supply CI's Postgres service and test settings. The authoritative PR
  CI must run those cases with its configured Postgres service.
- Built the complete multi-stage production Docker image successfully. This
  installs from the committed frontend lockfile, builds the SPA, installs the
  backend runtime, and copies the built SPA into the final Python image.

**Final verification:** 5 frontend test files / 22 tests passed; TypeScript and
Vite production build passed; 144 backend tests passed locally with 172
environment-gated skips; full production container build passed; npm reported
0 vulnerabilities; final diff and worktree checks passed.

## Final outcome

The frontend cohesion work is complete on this branch. Shared server-resource
policy now has one home in the hooks/query-key layer, repeated consumers use
that layer, the frontend has a CI regression net, related routes have clear
source ownership, and repeated clipboard lifecycle code has one tested owner.
The work intentionally does not add or change Azure scaling, Postgres queue,
leader-election, IMAP ingestion, or deployment behavior.

This branch is ready for review and merge into `master`. It does not by itself
make `v0.1.4-beta` ready to deploy: the scale-out branch must still be rebased
onto the resulting `master`, conflicts resolved against the new hooks and page
locations, and its own queue/replica/Azure verification completed.

## Claude continuation checklist

1. Review and merge `refactor/frontend-cohesion` into `master` through the
   normal PR process; require both CI jobs to pass, especially the backend job
   with Postgres that runs the locally skipped integration tests.
2. Do not squash away this handoff before recording the final merged commit or
   PR reference. The small commits deliberately separate test infrastructure,
   hook introduction, consumer migration, page organization, and final audit.
3. Update local `v0.1.4-beta` from its remote, create a safety/reference branch,
   and rebase it onto the newly merged `master`. Do not rebase it onto this
   feature branch before the review result is known.
4. Resolve frontend conflicts by retaining the canonical `queryKeys` and
   resource-hook ownership introduced here. Adapt beta-only consumers to these
   hooks rather than restoring inline duplicate queries.
5. Account for moved source files when resolving conflicts:
   platform-admin routes live in `pages/admin/`; domain report routes live in
   `pages/domain-detail/`.
6. Keep beta-only scale-out concerns separate: Postgres queue correctness,
   replica-safe scheduling/leadership, IMAP ingestion, Azure Bicep, managed
   identity/secrets, health probes, graceful shutdown, and autoscaling rules
   still need their own review and tests after the rebase.
7. After the rebase, run the full CI suite, build the production image, and run
   multi-replica integration tests before calling the Azure scale-out work
   deployment-ready.

## Conflict guide for the beta rebase

- If beta changes a component migrated here, keep beta's new user-facing
  capability but obtain shared domains, organization, mailbox, onboarding,
  users, or admin resources through the matching hook in `frontend/src/hooks/`.
- If beta adds invalidations, use the matching builder from `queryKeys.ts`; add
  a new canonical key there when the resource is genuinely new.
- Keep feature-local report/chart/filter queries beside their sole consumer.
  Promote them to a resource hook only when beta introduces another consumer or
  shared cache/refetch policy.
- Preserve route URLs from `App.tsx`; Chunk 4 moved source files only and did
  not authorize URL changes.
- Preserve the exact Node 20-compatible test versions unless the production
  container is deliberately upgraded and tested at the same time.

## Handoff entry point

1. Read this file and `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`.
2. Check `git status` and the latest commits on `refactor/frontend-cohesion`.
3. All planned chunks are complete; begin with branch review/merge and then the
   beta rebase checklist above. Do not add unrelated scale-out changes to this
   completed frontend branch.
4. If review requires a correction, record its commit and fresh verification
   results in this file before merging.

## Known local-environment issue

The frontend `package-lock.json`, `node_modules`, and TypeScript build cache had
been created by a container as user `nobody`, so normal host-side npm writes
initially failed with `EACCES`. This was workspace ownership, not a source or
build failure; the stale artifacts were safely recreated during Chunk 1. Keep
using the repository's Node 20 container for reproducible final verification.
