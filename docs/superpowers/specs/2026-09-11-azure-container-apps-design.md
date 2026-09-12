# Azure Container Apps Deployment — Design

**Date:** 2026-09-11
**Status:** Approved for planning
**Target release:** v0.1.5 (from `v0.1.5-beta`, sub-project 2 of 2 — built on top of the already-merged Postgres-queue scale-out work)

## Motivation

Sub-project 1 (`v0.1.5-beta1`) made the `worker` and `api` containers safe to run as N replicas. This sub-project delivers the actual managed, autoscaling Azure deployment that work exists to unblock: a one-click "Deploy to Azure" button standing up Container Apps, a private PostgreSQL Flexible Server, Key Vault, and KEDA-driven autoscaling.

A prior, now-superseded branch (`v0.1.4-beta`, commit `75c56d0`) already built a comprehensive version of this — ~2830 lines across 14 files, `bicep build`-clean and JSON-validated, but never live-deployed. It is being **critically ported**, not copied wholesale: each piece is re-derived against the current codebase and revised where real gaps or oversizing were found, per explicit direction during brainstorming. Two concrete data points anchored the reuse decision: the old design's KEDA autoscale query (`SELECT count(*) FROM background_jobs WHERE status = 'pending' AND run_after <= now()`) and worker health-probe port (`:8080/health`) already match sub-project 1's actual implementation exactly, despite predating it — strong evidence the underlying architecture is sound, even though specific parameters (network sizing, Key Vault access model) needed real reconsideration.

## Goals

- A "Deploy to Azure" button that stands up a working, private, autoscaling deployment in one pass, with a sizing-tier picker (Test/Small/Medium/Large) that pre-fills every size-dependent parameter — not a dozen independent numeric fields a first-time deployer has to reason about individually.
- Right-sized networking. The old design's `/16` VNet for four small subnets was real over-allocation, not a defensible default — this design fixes that at the source (tiered VNet/subnet CIDRs), not just by shrinking one number.
- Key Vault reached via a private endpoint, consistent with Postgres already being VNet-private-only — the old design's public-endpoint-plus-RBAC approach is explicitly superseded, not just noted as an alternative.
- Every setting sub-project 1 added is a deliberate decision here (wired with an explicit value, or deliberately left at its code default), not a blind gap.
- Graceful shutdown on SIGTERM — parked during sub-project 1 specifically because ACA revision swaps are exactly the container-lifecycle event it matters for. In scope here.
- Advisory (not authoritative) validation before a live deploy: `bicep build` (compile-time) and `az deployment group what-if` (dry-run) are what this plan's implementation can actually exercise without a live Azure subscription. A real `az deployment group create` against the user's own subscription remains the true end-to-end test, run by the user when ready — same honest framing the old design used.

## Non-goals

- Changing anything about the worker/queue/rate-limiter application code sub-project 1 already built — this is deployment infrastructure only.
- IMAP / Google Workspace ingestion (product roadmap Phase 3, unrelated).
- Multi-region deployment, disaster recovery, or geo-redundant Postgres — out of scope for a first managed-deployment offering; the sizing tiers stop at "Large," not "highly available."
- A live Azure deployment as part of this plan's own execution — no subagent in this environment has Azure credentials or a subscription to deploy into. The plan's implementers validate via `bicep build`/`what-if`; the user performs and reports back on the actual first live deploy.

## Design

### 1. Sizing tiers — the deploy wizard's central decision

A `deploymentSize` parameter (`test` | `small` | `medium` | `large`, Portal dropdown + CLI parameter, default `small`) drives every size-dependent value below via a single lookup table in `main.bicep`. Each tier's values remain individually overridable through an Advanced section in `createUiDefinition.json` (and always overridable via CLI/parameters file) — the tier is a sensible starting point, not a lock.

| | Test | Small | Medium | Large |
|---|---|---|---|---|
| VNet | `/25` | `/24` | `/23` | `/22` |
| `aca` subnet | `/27` | `/26` | `/25` | `/23` (Microsoft's own ACA production-scale guidance) |
| `postgres` subnet | `/28` | `/28` | `/28` | `/27` |
| `keyvault-pe` subnet | `/29` | `/28` | `/28` | `/28` |
| Postgres SKU / tier | `Standard_B1ms` / Burstable | `Standard_B2s` / Burstable | `Standard_D2ds_v4` / General Purpose | `Standard_D4ds_v4` / General Purpose |
| Postgres storage | 32 GB | 32 GB | 64 GB | 128 GB |
| `api` min/max replicas | 1 / 1 | 1 / 2 | 1 / 3 | 2 / 5 |
| `worker` min/max replicas | 1 / 1 | 1 / 2 | 1 / 3 | 1 / 5 |
| Log Analytics retention | 30 days | 30 days | 60 days | 90 days |

All four subnets in every tier fit comfortably inside that tier's VNet with room to spare — concrete CIDR allocation (base addresses per subnet within the VNet block) is an implementation-plan detail, not re-litigated here. This replaces the old design's flat `/16` VNet / `/23` `aca` subnet applied uniformly regardless of deployment size. `/28` is a floor, not a starting point that shrinks further for `test`: Azure's documented minimum delegated-subnet size for a Postgres Flexible Server is `/28` (16 addresses) — a smaller `postgres` subnet is rejected at deployment time, so `test` uses the same `/28` as `small`/`medium` rather than a tighter `/29`.

**Wizard control:** `createUiDefinition.json` renders the tier picker as `Microsoft.Common.OptionsGroup` (Azure Portal's radio-button group, not a dropdown) — all four tiers visible and their trade-off legible at a glance, rather than hidden behind a click. Each option's label states what that tier actually provisions, not just its name, e.g.:

- **Test** — smallest footprint: single `api`/`worker` replica, Burstable B1ms Postgres, /25 VNet. For trying it out, not real traffic.
- **Small** — a few users: up to 2 `api`/`worker` replicas, Burstable B2s Postgres. The default.
- **Medium** — a small team/MSP: up to 3 `api`/`worker` replicas, General Purpose D2ds_v4 Postgres.
- **Large** — heavier load: 2-5 `api` replicas, up to 5 `worker` replicas, General Purpose D4ds_v4 Postgres, /22 VNet sized to Microsoft's own ACA production subnet guidance.

Exact option-label wording is an implementation-plan detail; the control type (radio group, not dropdown) and the "name plus inline description, not name alone" requirement are locked in here.

### 2. Postgres High Availability — an independent add-on, not tied to any tier

A `postgresHighAvailability` checkbox (`Microsoft.Common.CheckBox`), independent of the `deploymentSize` radio group, controls Postgres Flexible Server's built-in HA. This is deliberately an orthogonal axis, not a fifth tier or a property locked to `large` — availability posture and compute size are different decisions, and coupling them would remove a real, legitimate choice in both directions (a `test` deployer validating failover; a `large` deployer with their own DR strategy skipping it).

**What it actually does, technically:** unchecked → `highAvailability.mode: 'Disabled'` (the old design's only option, and this design's default everywhere). Checked → `highAvailability.mode: 'ZoneRedundant'`, `standbyAvailabilityZone` left unset for Azure to auto-select — Azure then manages a synchronously-replicated standby in a different Availability Zone with automatic failover (typically RPO=0, RTO under ~120s). The app's `DATABASE_URL`/FQDN never changes across a failover, so **no `api`/`worker` code changes are needed** — this is purely a Postgres-module Bicep property. The cheaper `SameZone` mode (protects against node failure, not a zone outage, and works in non-AZ regions) stays available as a manual override via the parameters file/CLI, not exposed as a second wizard control — keeps the Portal UI to one checkbox rather than a tri-state control.

**Description text shown next to the checkbox** (exact wording is an implementation-plan detail, substance locked in here): explains that this adds a synchronously-replicated standby in a second Availability Zone with automatic failover, and states plainly that it **roughly doubles Postgres compute cost** — a visible cost callout, not a silent checkbox.

**Availability-zone region support:** rather than building wizard logic to pre-detect whether the selected region supports Availability Zones (real added complexity — `Microsoft.Solutions.ArmApiControl` region-capability lookups), an incompatible region/HA combination surfaces as Azure's own deployment-time validation error, documented as a known constraint in `deploy/azure/README.md`.

**Interaction with `deploymentSize`:** the checkbox is hidden for `test` **and** `small` — not just `test`. `test`'s reason is the cost-pairing one above; `small` is a hard Azure constraint discovered during implementation review, not a judgment call: both tiers run Postgres on the `Burstable` compute tier (`Standard_B1ms`/`Standard_B2s`), and Azure Database for PostgreSQL Flexible Server does not support zone-redundant HA on `Burstable` at all — only `GeneralPurpose` and `MemoryOptimized` do. Offering the checkbox there would let a deployer select a combination Azure rejects at deployment time. Available and unchecked by default for `medium`/`large` (both `GeneralPurpose`). No other tier-driven parameter needs to change as a direct consequence of toggling it — Postgres HA duplicates whatever SKU/storage the selected tier already specifies; it doesn't require a bigger SKU or affect `api`/`worker` replica counts, backup retention, or Log Analytics retention. If the implementation plan finds a concrete reason a specific pre-fill coupling is actually needed, it can add one — none was identified during design.

### 3. Key Vault: private endpoint (supersedes the old public-endpoint-plus-RBAC design)

A new, non-delegated `keyvault-pe` subnet (private endpoints and service delegation are mutually exclusive on one subnet) hosts a private endpoint for Key Vault, plus a `privatelink.vaultcore.azure.net` private DNS zone and VNet link — mirroring the pattern the network module already uses for Postgres's `privatelink.postgres.database.azure.com` zone. RBAC-based access control (scoped to the apps' user-assigned managed identity) stays as the authorization model; only the network reachability path changes, from "public endpoint, gated by RBAC" to "VNet-private, gated by RBAC."

### 4. Everything else the old design got right, ported with light adaptation

- **VNet + Postgres module structure**: delegated subnets, private DNS zone dependency ordering (the postgres module takes the network module's zone-link output as a parameter specifically so Bicep sequences the zone-VNet link before the server that needs it) — this pattern carries forward unchanged, just re-parametrized by tier.
- **Container Apps environment + Log Analytics**: unchanged structurally; retention days becomes tier-driven.
- **`api` app**: external HTTPS ingress, HTTP-concurrency autoscale, resolver sidecar. `RATE_LIMIT_BACKEND=postgres` stays hardcoded in its env (already correct for any multi-replica deployment, which this always is).
- **`worker` app**: internal (no ingress), KEDA Postgres-queue-depth autoscale (`SELECT count(*) FROM background_jobs WHERE status = 'pending' AND run_after <= now()`, already an exact match to `app/repositories/jobs.py::claim_one_job`'s claim predicate — confirmed during brainstorming, not assumed), resolver sidecar, liveness/startup probes against `:8080/health` (exact match to sub-project 1's `app/workers/health.py`). Min-1 replica preserved — a leader must always exist.
- **Resolver sidecar pattern**: unchanged — ACA has no UDP ingress, so DNS resolution stays over `127.0.0.1` inside each app's pod, exactly as before.
- **`migrate` ACA Job + deploymentScript**: structurally unchanged (creates the `dmarc_app` role, runs Alembic, bootstraps the platform admin, gates the apps starting until it succeeds).
- **New env vars from sub-project 1 — explicit per-variable decisions, not a blanket carry-forward**: `LEADER_DATABASE_URL` and `DATABASE_READ_URL` are deliberately left unset (no connection pooler or read replica in this design — both default sensibly to the primary connection). `WORKER_CONCURRENCY`/`WORKER_QUEUE_POLL_INTERVAL_SECONDS`/`WORKER_JOB_STALE_SECONDS` stay at their code defaults unless the implementation plan finds a concrete reason a given tier needs different values (e.g., `Large`'s higher `workerMaxReplicas` interacting with the default queue-poll interval) — not wired speculatively.

### 5. New work the old design didn't have

- **`backend/app/scripts/ensure_app_role.py` needs recreating**, not copying — it doesn't exist on the current tree (confirmed by direct check during brainstorming). Managed Postgres Flexible Server has no `docker-entrypoint-initdb.d` equivalent, so the `dmarc_app` role creation the self-hosted path handles via `db/init/01-create-app-role.sh` needs an equivalent one-shot script the `migrate` Job runs first. The implementation plan derives this from `db/init/01-create-app-role.sh`'s current actual SQL (idempotent role creation + password set + `GRANT CONNECT`), not from the old branch's version of the script.
- **`createUiDefinition.json`'s deployment-size picker and HA checkbox** are both new UI surface, not present in any form in the old design (which had no SKU/sizing/HA exposure in the Portal wizard at all — the old ARM template didn't even have `highAvailability` as a parameter, and its sizing-relevant parameters existed but were CLI-only).
- **Graceful shutdown on SIGTERM** (`app/workers/scheduler.py`): install a `signal.SIGTERM` handler that cancels the running task set so the existing `finally: await leader.release()` actually executes on a normal `docker stop`/ACA revision swap, instead of only on an unhandled crash. This was explicitly parked during sub-project 1's final review and routed here; it's now in scope. Ideally also marks any in-flight job back to `pending` on cancellation rather than leaving it for the stale-job reaper (bounded by `WORKER_JOB_STALE_SECONDS`) to find it later.

## Testing

- `bicep build` on every module and the root template — compile-time validation, catches type/reference errors.
- `az deployment group what-if` against a real (but empty) resource group — the closest thing to a dry-run without actually provisioning; requires Azure credentials the implementation plan's own subagents won't have, so this step is documented as something the user runs, not something a subagent verifies automatically. Implementers instead lean on `bicep build`'s validation plus careful manual review against Azure's documented resource schemas.
- `createUiDefinition.json` gets validated via the Azure sandbox's own JSON schema check (`https://portal.azure.com/#blade/Microsoft_Azure_CreateUIDef/SandboxBlade` is the interactive tool; offline, a JSON-schema-shaped review during implementation is the fallback).
- The SIGTERM handler gets a real automated test (send the process a SIGTERM in a subprocess/test harness, confirm `leader.release()` ran and any in-flight job's status reflects graceful handling) — this piece, unlike the Bicep itself, runs entirely in this repo's existing Python test suite and needs no Azure access.
- The true end-to-end test — an actual `az deployment group create` against a real subscription, confirming the migrate job succeeds, the api responds on `/api/health`, and the worker shows a leader elected with jobs draining — is performed by the user after this plan's implementation lands, not by this plan's own execution.

## Sequencing / branch plan

Continues on `v0.1.5-beta` (already has sub-project 1's work merged). This spec's implementation plan is a separate set of tasks appended to that branch's history, not a new branch — matching how sub-project 1 itself was one continuous branch rather than per-spec branches.

## Risks

- **Bicep API version drift.** This design carries forward the old branch's already-pinned API versions (`Microsoft.App/containerApps@2024-03-01`, `Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01`, etc.) rather than attempting to verify newer ones exist — this environment has no live way to check current Azure API versions. Mitigated by: these were deliberately pinned, reasonably recent versions when chosen, and Azure API versions are backward-compatible within a resource type's stable channel; a stale-but-valid version is a "works, not on the newest features" risk, not a broken-deployment risk.
- **Never live-deployed.** Both the old design and this port are validated only at compile-time/dry-run within this plan's own execution. The first real deploy remains the true test, same risk the old design carried and disclosed honestly.
- **Sizing-tier numbers are a first approximation**, not load-tested. They're reasoned defaults (Postgres SKU escalation, ACA replica ceilings, VNet right-sizing per Microsoft's own subnet guidance for the `aca` tier), reviewed and approved during brainstorming, but real usage patterns may reveal a tier's default is meaningfully off in either direction. Low cost if wrong — they're parameters, not structural decisions, and stay user-overridable.
- **Zone-Redundant HA in a non-AZ region fails at deployment time, not at wizard-selection time.** Deliberately accepted (section 2) rather than building region-capability-aware wizard logic — Azure's own validation error is the failure mode, documented as a known constraint rather than engineered around.

## Out of scope (tracked elsewhere)

- Multi-region deployment. Same-region zone-redundant HA is now in scope (section 2) — multi-region (a second deployment in a different Azure region, cross-region replication/failover) is a materially larger undertaking and stays out of scope here.
- IMAP / Google Workspace ingestion (product roadmap Phase 3).
- Any application-code change to the worker/queue/rate-limiter (sub-project 1's completed scope).
