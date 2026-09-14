# Horizontal Scale-Out — Design

**Date:** 2026-09-11
**Status:** Approved for planning
**Target release:** v0.1.5 (from the `v0.1.5-beta` branch, forked from `v0.1.4` stable)

## Motivation

The backend and frontend cohesion refactors (v0.1.4) removed the last structural
blocker to this work: every database query now goes through `app/repositories/`,
which is exactly the seam this design needs (a job-queue repository, a
rate-limit repository, an optional read-replica routing point) without
retrofitting scattered inline queries first.

Today, neither tier of the app is safe to run as more than one replica:

1. **`app/workers/scheduler.py`** is explicitly single-instance by design — its
   own docstring says "no distributed locking needed since there's only ever
   one worker replica." Running two would double-fire every job.
2. **`app/services/auth/rate_limit.py`** is a pure in-memory sliding window
   with a comment acknowledging "each replica will only count its own
   traffic" — multiple `api` replicas would each enforce the login-throttle
   independently, undermining it.

This blocks two goals: self-hosters who want to run more than one worker or
api container for throughput or availability today, and the future Azure
Container Apps deployment (a separate, later spec) which needs N-replica-safe
containers as a precondition.

This branch's scope is deliberately built by **porting**, not
re-designing, work that already existed on the (now-superseded)
`v0.1.4-beta` branch, commit `fe424bd` ("Scale out the worker + api:
Postgres work queue, leader election (Phase 1)") — a well-scoped, tested,
and live-verified implementation of this exact design. It is adapted here
to the post-refactor repository-layer conventions rather than reused
byte-for-byte, since its original form predates that refactor.

## Goals

- Any number of `worker` replicas can run concurrently against the same
  Postgres database with no double-processed jobs and no message broker —
  `docker compose up -d --scale worker=N` becomes safe.
- Automatic failover: if the elected leader replica dies, another takes over
  without operator intervention.
- Any number of `api` replicas can run concurrently with a correctly shared
  login rate limiter.
- Stay scalable from a single homelab container up through hundreds or
  thousands of containers, **without requiring new infrastructure for the
  homelab case** — every piece introduced here is either always-on
  (the job queue, since the worker needs *some* coordination mechanism the
  moment it's more than one instance) or an opt-in environment variable that
  does nothing when unset.
- Name the real scale levers honestly, including the ones this design does
  NOT build, so a future operator or future plan knows what to reach for and
  why it isn't here yet.

## Non-goals

- **Azure Container Apps infrastructure** (Bicep modules, the Deploy-to-Azure
  button, KEDA autoscaling, the resolver sidecar, managed identity/Key Vault
  wiring). That is sub-project 2, its own spec, built on top of this one.
- **Postgres horizontal sharding (Citus/Hyperscale).** Explicitly considered
  and deferred — see "Deferred: sharding" below. Not started in this plan.
- **IMAP / Google Workspace ingestion.** Unrelated, remains Phase 3 on the
  product roadmap.
- Reworking anything about DMARC/TLS-RPT ingestion logic itself, RLS policy
  design, or the frontend. This is worker/API infrastructure only.

## Design

### 1. Worker: Postgres-backed job queue + leader election

Replace the single in-process `AsyncIOScheduler` with a `background_jobs`
table. Every worker replica runs consumer loops that claim pending jobs with
`SELECT ... FOR UPDATE SKIP LOCKED`, so any number of replicas can drain the
queue concurrently without ever double-processing a row. Exactly one replica
self-elects **leader** via a Postgres advisory lock (`pg_try_advisory_lock`)
and owns two responsibilities: enqueueing due recurring work (the same jobs
`scheduler.py` runs today — mailbox polls, DNS sweeps, retention purge,
domain-verification sweep, hosted-reports poll, update check) and reaping
jobs stranded by a worker that crashed mid-processing. If the leader dies,
its dedicated connection drops, Postgres releases the advisory lock
automatically, and another replica acquires it on its next attempt — failover
with no extra coordination service.

**New model:** `app/models/background_job.py` — `BackgroundJob`
(`job_type`, `dedupe_key` nullable, `payload: JSONB`, `status` enum,
`run_after`, `attempts`/`max_attempts`, `locked_by`/`locked_at`,
`last_error`). Deliberately **not** RLS-scoped and has no `organization_id`
column (any org id a job needs lives inside `payload`) — this is
worker-owned infrastructure in the same category as `UpdateCheckState`, not
tenant data, so it's correctly excluded from the RLS meta-guard in
`tests/test_rls.py`. A partial unique index on `dedupe_key WHERE status IN
('pending','running')` makes re-enqueuing an already-queued recurring job a
no-op, so a queue that's temporarily behind never piles up duplicates.

**New repository file:** `app/repositories/jobs.py` — per this project's
existing convention (`app/repositories/admin_updates.py` is the precedent:
a repository file for worker/admin-internal infrastructure with no 1:1
router, not just tenant-facing resources). Owns `claim_one`, `enqueue`
(dedupe-aware), `complete`, `fail` (with backoff via `run_after`), and
`reap_stalled`. This is the one deliberate structural change from the
ported design, which had these queries inline in `services/jobs/queue.py` —
moving them keeps the whole backend's "every query lives in
`app/repositories/`" property true going forward instead of reopening it.

**Leader election:** `app/services/jobs/leader.py`, ported close to
verbatim — it's connection-lifecycle code (matching `app/db/rls.py`'s
existing exclusion from the repository layer: session/transaction
management, not data access). Holds its advisory lock on a dedicated
`NullPool` connection so its lifecycle is exactly the leader's own and it
never competes with the worker's normal connection pool. **This
dedicated connection must always bypass PgBouncer** when PgBouncer is in
use (see the PgBouncer section below) — `pg_advisory_lock` is
session-scoped, and PgBouncer's transaction-pooling mode would silently
return the underlying connection to the pool between statements, breaking
leadership. The design already isolates this connection for its own
reasons, so this is a one-line documentation requirement, not new code.

**Scheduler rewrite:** `app/workers/scheduler.py` is rewritten against its
current (post-refactor) form — the existing job functions
(`run_domain_verification_sweep`, `run_dns_check_sweep`,
`run_retention_purge`, `poll_hosted_reports_mailbox`, `poll_org_mailbox`,
`run_update_check`) are reused completely unchanged as queue handlers; only
the dispatch mechanism around them changes.

**Liveness endpoint:** `app/workers/health.py`, `:8080/health` — reports
unhealthy if the event loop stalls (not just if the process crashes), wired
as a docker-compose healthcheck so a wedged replica gets recycled, and later
usable as an ACA liveness probe.

**Delivery semantics:** at-least-once. Safe because every job handler is
already idempotent — this is precisely what the just-finished
repository-extraction refactor hardened (`insert_aggregate_report_if_new` /
`insert_forensic_report_if_new` / `insert_tls_rpt_report_if_new`'s
`IntegrityError`-based idempotent insert, upserts elsewhere). No handler
changes needed for this property; it already holds.

### 2. API: pluggable rate limiter

`app/services/auth/rate_limit.py`'s `SlidingWindowLimiter` stays the default
(in-memory, zero config, correct for a single api replica). Add a
Postgres-backed shared sliding-window implementation, selected via
`RATE_LIMIT_BACKEND=postgres`, for operators running more than one `api`
replica. New model + migration for `rate_limit_hits`; its queries live in a
new `app/repositories/rate_limits.py`. Login/OTP endpoint volume is tiny, so
this comfortably reuses Postgres rather than needing its own store.

### 3. PgBouncer support (optional)

Purely a deployment-time option: point `DATABASE_URL` at a PgBouncer
instance instead of Postgres directly. No required app code changes for the
general case — RLS's `SET LOCAL` context-setting is transaction-scoped and
already compatible with transaction-mode pooling. The one exception is the
leader's dedicated advisory-lock connection (see above), which must be
configured to connect directly to Postgres, bypassing the pooler, regardless
of what `DATABASE_URL` points the rest of the app at. This gets a named
settings key (`LEADER_DATABASE_URL`, defaulting to `DATABASE_URL` when
unset) so the direct-connection requirement is explicit and enforced by
config rather than left as tribal knowledge. Undocumented and unused by
homelab deployments — no compose profile changes to the default path.

### 4. Optional read-replica routing

`app/db/session.py` currently has one `engine` / `async_session_factory`
pair. Add a second, optional pair built from a new `DATABASE_READ_URL`
setting (`None` by default). A new FastAPI dependency, `get_read_db`
(parallel to the existing `get_db`), yields a session from the replica
engine when `DATABASE_READ_URL` is set, or transparently falls back to the
primary engine when it isn't — so this is invisible to a homelab deployment
with no replica configured.

The routing decision lives entirely at the **router/dependency-injection
layer**, not inside repository functions — a repository function takes
whatever `AsyncSession` it's handed and has no primary-vs-replica awareness,
exactly preserving the just-finished repository layer's signatures and
separation of concerns. Only the read-only, latency-tolerant report/analytics
GET endpoints switch from `Depends(get_db)` to `Depends(get_read_db)`:
`dmarc_reports.py`'s summary/by-day/grouped/trend/posture endpoints and
their repository-layer equivalents are the concrete target set; the
implementation plan enumerates the exact function list. Endpoints where a
user might reasonably expect to see a write they just made reflected
immediately (e.g. re-fetching a domain right after creating it) stay on
`get_db`.

RLS correctness on a replica: a Postgres streaming replica (including Azure
Database for PostgreSQL Flexible Server's managed read replicas) carries the
same schema and RLS policies as the primary, and `SET LOCAL
app.current_org_id` behaves identically on a read-only replica session — no
special-casing needed. A streaming replica also rejects writes at the
Postgres protocol level, so an accidental write through `get_read_db` fails
loudly with a clear Postgres error rather than silently succeeding somewhere
unexpected — a correctness backstop that requires no application code.

### Deferred: sharding (Citus / Hyperscale)

Not built in this plan. A single well-provisioned Postgres primary (Azure
Database for PostgreSQL Flexible Server scales to 96 vCores / hundreds of GB
RAM on its larger SKUs) comfortably handles this app's OLTP-plus-reporting
workload at a scale far beyond what a DMARC dashboard's inherently bounded
report volume (daily/weekly aggregate reports per domain, not a
high-frequency event stream) would ever generate — including at "thousands
of containers" on the stateless api/worker tiers. The data model already
partitions everything by `organization_id` via RLS, which is exactly the
shard key Citus would want, so this door stays open without any design
changes here if it's ever genuinely needed. Revisit only if a single primary's
vertical ceiling is demonstrably the bottleneck, not before.

## Testing

Real-Postgres integration tests (gated on `TEST_DATABASE_URL`, matching this
project's existing convention), ported and adapted from the original
implementation's test suite:

- `SKIP LOCKED` never double-claims a job under concurrent claimers.
- Dedupe: re-enqueuing a job with an in-flight `dedupe_key` is a no-op.
- Retry/backoff timing via `run_after`, capped at `max_attempts`.
- Stalled-job reclaim (a job `locked_by` a replica that never completed it
  gets reaped and re-queued).
- Leader singleton: two `LeaderLock` instances racing for the same key,
  exactly one wins; killing the leader's connection hands leadership to the
  other.
- Postgres-backed rate limiter's shared sliding window is correct across
  concurrent callers.
- `get_read_db` falls back to the primary engine when `DATABASE_READ_URL` is
  unset; routes to the replica engine when it is (using a second test
  Postgres connection standing in for a replica, since a real streaming
  replica isn't practical in CI — the routing logic itself is what's under
  test, not real replication lag).

Plus a live multi-replica smoke check before calling this done:
`docker compose up -d --scale worker=3`, confirm exactly one leader elected,
kill it, confirm another takes over automatically and in-flight jobs are
reclaimed.

## Sequencing / branch plan

All of this lands on `v0.1.5-beta` (forked from `v0.1.4` stable, already
created). This spec is sub-project 1 of 2 — sub-project 2 (Azure Container
Apps: Bicep modules, Deploy-to-Azure button, KEDA autoscaling) gets its own
spec and plan afterward, built on top of this one, once this lands and is
verified.

## Risks

- **Leader-election correctness under PgBouncer misconfiguration.** If an
  operator points `LEADER_DATABASE_URL` at a pooler by mistake, leadership
  could flap. Mitigated by defaulting `LEADER_DATABASE_URL` to `DATABASE_URL`
  (correct for the no-pooler homelab case) and documenting the requirement
  explicitly wherever PgBouncer setup is documented.
- **At-least-once delivery relies on handler idempotency holding forever.**
  A future job handler that isn't idempotent would be unsafe under this
  queue. Mitigated by the existing repository-layer convention (every insert
  path uses the `IntegrityError`-based or upsert idempotent pattern) and by
  naming this explicitly in the implementation plan's global constraints.
- **Read-replica staleness surprising a user.** Scoped down specifically to
  avoid this — only latency-tolerant report/analytics reads route to a
  replica; anything where a user expects to see their own just-made write
  stays on the primary.

## Out of scope (tracked elsewhere)

- Azure Container Apps deployment (sub-project 2, future spec).
- Postgres sharding (documented above as a deferred, not abandoned, future
  path).
- IMAP / Google Workspace ingestion (product roadmap Phase 3).
