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

**Status:** in progress

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

**Next:** migrate Onboarding, settings components, policy builders, Team, and
domain-detail consumers; replace raw invalidation arrays with `queryKeys`.

### Chunk 4 — focused component and page organization

**Status:** pending

### Chunk 5 — final verification and handoff

**Status:** pending

## Resume instructions

1. Read this file and `docs/superpowers/specs/2026-09-05-cohesion-refactor-design.md`.
2. Check `git status` and the latest commits on `refactor/frontend-cohesion`.
3. Resume the first ledger chunk still marked `in progress` or `pending`.
4. Do not assume a chunk is complete unless its verification results and commit
   hash are recorded here.

## Known local-environment issue

The existing frontend `package-lock.json`, `node_modules`, and TypeScript build
cache were created by a container as user `nobody`, so normal host-side npm
writes initially failed with `EACCES`. This is workspace ownership, not a source
or build failure.
