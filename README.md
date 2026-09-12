# YetAnotherDmarcTool

[![CI](https://github.com/BJD1997/YetAnotherDmarcTool/workflows/CI/badge.svg)](https://github.com/BJD1997/YetAnotherDmarcTool/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted, multi-tenant DMARC / TLS-RPT reporting dashboard. It ingests
aggregate and forensic DMARC reports plus TLS-RPT reports from a shared
mailbox, turns them into readable analytics and a 0–100 domain rating, and
continuously checks a domain's real DNS (SPF, DKIM, DMARC, DMARCbis, MX,
MTA-STS, DANE, STARTTLS, TLS-RPT) against best practice — with a guided
policy builder to move a domain from "just monitoring" to `p=reject` safely.

Built as a hobby project: I wanted a self-hosted tool that actually covered
all of this in one place, couldn't find one, so built it (with Claude's
help). It's not run as a commercial service —
multi-tenant is just an MSP habit: after years of that mindset, anything
worth building gets built as if it should support more than one tenant,
even at a scale of one.

It's read-only and advisory toward the domains it watches: it never writes
records on an organization's own DNS. The one exception is its own
operator-hosted reporting mailbox feature, which can auto-provision the
RFC 7489 §7.1 authorization record it needs on *its own* domain — never on
another organization's.

## Live demo

**[demo.yetanotherdmarctool.com](https://demo.yetanotherdmarctool.com)** —
a public, read-only demo running against this project's own domain
(`yetanotherdmarctool.com`), so the DNS checks, ratings, and policy builder
you see are real, not fixtures.

```
email:    demo@yetanotherdmarctool.com
password: lantern-maple-falcon-willow-989
```

It's a fully separate deployment with its own database — nothing you do
there touches real data, and every state-changing request is rejected
server-side regardless of what the UI lets you click.

## Features

- **Report ingestion** — DMARC aggregate + forensic reports and TLS-RPT
  reports, pulled from a Microsoft 365 shared mailbox via Microsoft Graph, or
  from YetAnotherDmarcTool's own operator-hosted mailbox for organizations
  with none of their own to dedicate.
- **Day-grouped report browser** with per-record drill-down (source IP,
  disposition, SPF/DKIM alignment, plain-English pass/fail narratives).
- **DNS best-practice checks** — SPF, DKIM, DMARC, DMARCbis (RFC 9989), MX,
  MTA-STS, DANE, STARTTLS, TLS-RPT — run on a schedule for every verified
  domain, or on demand.
- **Domain rating** — a single 0–100 score + letter grade synthesizing check
  results and observed DMARC pass rate.
- **Policy builder** — DNS record generators (DMARC, MTA-STS) and a rollout
  recommendation engine that only suggests tightening `p=` once pass rate,
  stability, and sender review are actually there.
- **Sender inventory & action queue** — identifies what's actually sending
  mail as a domain (Microsoft 365, known ESPs, etc.), lets you approve/block
  senders, and surfaces what needs attention instead of a raw report list.
- **Detected domains & DKIM selectors** — proactively surfaces subdomains
  sending real mail that were never registered, and DKIM selectors seen in
  reports that aren't tracked yet — instead of staying silent until something
  breaks.
- **Multi-tenant**, enforced at the database layer (Postgres row-level
  security), not just in application code.
- **Two auth paths**: Microsoft Entra SSO (delegated OIDC) for organizations
  that use Microsoft 365, and local email + password + TOTP for everyone else —
  chosen automatically per organization.

## Architecture

```
                         ┌─────────────┐
                         │   Browser   │
                         └──────┬──────┘
                                │ HTTPS (reverse-proxied)
                         ┌──────▼──────┐
                         │     api     │  FastAPI, serves the built SPA too
                         └──┬───────┬──┘
                            │       │
                 ┌──────────▼─┐   ┌─▼──────────┐
                 │  postgres  │   │  resolver  │  dedicated Unbound instance
                 │  (RLS)     │   │ (DNSSEC-   │  — every DNS check query goes
                 └──────────▲─┘   │ validating)│    through this, not the
                            │     └────────────┘    host's own resolver
                 ┌──────────┴─┐
                 │   worker   │  N replicas, off a Postgres work queue: mailbox
                 │            │  polling, DNS check sweep, domain verification
                 └────────────┘  sweep, retention purge — no message queue
```

`api` and `worker` are the same Docker image (`backend/Dockerfile`) run with
different commands; a one-off `migrate` service (same image again) runs
Alembic migrations and bootstraps the first platform-admin account before
either starts. There's no separate frontend server — Vite builds the SPA at
image-build time and FastAPI serves the static output directly (`app/main.py`).

## Tech stack

### Backend

| Package | License | What it's for |
|---|---|---|
| `fastapi`, `uvicorn` | MIT, BSD-3-Clause | Web framework and ASGI server |
| `pydantic`, `pydantic-settings` | MIT | Request/response validation, typed env-var config |
| `sqlalchemy[asyncio]`, `asyncpg` | MIT, Apache-2.0 | Async ORM and Postgres driver |
| `alembic` | MIT | Schema migrations |
| `msal` | MIT | Microsoft Graph / Entra token acquisition (client-credentials for mailbox access) |
| `pyjwt[crypto]` | MIT | Verifies Entra SSO id_tokens (`PyJWKClient` against the tenant's real JWKS) |
| `cryptography` | Apache-2.0 OR BSD-3-Clause | Fernet encryption for secrets at rest, and RSA/Ed25519 key parsing for the DKIM checker |
| `argon2-cffi` | MIT | Local-auth password hashing |
| `pyotp`, `qrcode` | MIT, BSD-3-Clause | TOTP enrollment and verification for local auth |
| `email-validator` | Unlicense | Email address format validation |
| `parsedmarc` | Apache-2.0 | Parses DMARC aggregate/forensic and TLS-RPT report XML/JSON — used as a library only, not its CLI or Elasticsearch output writers |
| `dnspython` | ISC | Low-level DNS primitives the checkers are built on |
| `httpx` | BSD-3-Clause | HTTP client — Graph API calls, MTA-STS policy file fetches |
| `python-multipart` | Apache-2.0 | Pulled in by FastAPI for optional form-data parsing; not used directly anywhere in this app |
| `pytest`, `pytest-asyncio` | MIT, Apache-2.0 | Test suite (`backend/requirements-dev.txt`, dev-only — not part of the deployed runtime) |

### Frontend

| Package | License | What it's for |
|---|---|---|
| `react`, `react-dom` | MIT | UI |
| `react-router-dom` | MIT | Client-side routing |
| `@tanstack/react-query` | MIT | Server-state fetching/caching |
| `lucide-react` | ISC | Icons |
| `vite`, `@vitejs/plugin-react` | MIT | Build tooling (dev-only — not part of the deployed runtime) |
| `typescript` | Apache-2.0 | Type checking (`tsc -b` runs as part of every build) |

Every package actually shipped to the browser (`react`, `react-dom`,
`react-router-dom`, `@tanstack/react-query`, `lucide-react`, and their own
transitive dependencies) is MIT or ISC — fully permissive.

### Infrastructure

| Component | License | What it's for |
|---|---|---|
| PostgreSQL 16 | PostgreSQL License (permissive) | Primary datastore; row-level security is what actually enforces tenant isolation |
| [`mvance/unbound`](https://github.com/MatthewVance/unbound-docker) | BSD-3-Clause | A dedicated, DNSSEC-validating resolver every check query goes through, kept separate from the host's own resolver so its cache behavior can be tuned independently (see `resolver/overrides.conf`) |
| Docker Compose | Apache-2.0 | Orchestration — no Kubernetes, no message queue; a single `worker` replica is enough at this scale |

Every dependency is exact-pinned (`==`, not a range) in `backend/requirements.txt`
and `frontend/package.json` — deliberate, not an oversight: a range lets a
future `npm install`/`pip install` silently pull in whatever's newest at that
moment, good or compromised. New dependencies should be pinned to their
current latest stable/LTS release, never a range and never a preview/RC build.

## How it works

### Multi-tenancy

Every tenant-owned table has Postgres row-level security enabled and
*forced* (`FORCE ROW LEVEL SECURITY` — without this, the table's own owner
role bypasses RLS regardless of the policy). The app connects as a separate,
non-owner `dmarc_app` role specifically so RLS actually applies to it. Each
request sets `app.current_org_id` via `SET LOCAL` for the duration of one
transaction; a small number of cross-org background jobs (the DNS check
sweep, domain verification sweep, retention purge) instead set
`app.is_platform_admin = true`, which every policy also accepts.

### DMARC subdomain policy inheritance (RFC 7489)

A subdomain doesn't need its own `_dmarc.<subdomain>` TXT record to be
protected — DMARC lets it inherit the parent's `sp=` (or `p=` if `sp=` isn't
set). The DMARC checker knows this and scores accordingly instead of just
reporting "no record found" for every subdomain that relies on inheritance:

| Subdomain has own record? | Parent's policy | Subdomain check result |
|---|---|---|
| Yes | — | Scored on its own record, same as any apex domain |
| No | `p=reject` (or `sp=reject`) | `pass` — inherits `sp=reject`/`p=reject` |
| No | `p=quarantine` | `warn` — inherits quarantine, suggests moving to reject |
| No | `p=none` | `warn` — inherits none, "monitoring only, no enforcement" |
| No | No usable DMARC record either | `fail` — nothing protects this subdomain |

### Attributing report data to the right domain

A single DMARC aggregate report can legitimately bundle mail for an
organizational domain *and* any number of its subdomains under one
`policy_published/domain` — RFC 7489 §7.2 keeps that report-level field and
each record's own `header_from` deliberately separate, precisely because
subdomain mail is evaluated under the parent's inherited policy. Attributing
every record in a report to the report's own domain (the naive reading) folds
all of that subdomain traffic permanently into the parent's stats:

| Scenario | Where the record's volume ends up |
|---|---|
| `header_from` matches a registered domain exactly | That domain |
| `header_from` is a subdomain of a registered domain, itself unregistered | The nearest registered ancestor (visible under "detected domains" as a suggestion to register it) |
| `header_from` matches no registered domain at all, no registered ancestor either | Nowhere yet — surfaced as "detected domains" |

Every record is matched by its own `header_from` at ingestion, not by the
report's `policy_published/domain` — the report-level domain is still tracked
separately (used for report browsing and policy-stability calculations, which
genuinely are report-level concerns), just not used for per-record attribution.

### SPF's `~all` vs `-all` — conditional mode

There are two philosophies for SPF's catch-all mechanism, selectable per
organization:

| Mode | Behavior |
|---|---|
| **Strict** | Always recommends `-all` (hardfail) — the traditional, unconditional advice |
| **Conditional** | Recommends `-all` *until* the domain's DMARC policy (its own, or inherited from its parent) reaches `quarantine`/`reject` — at that point DMARC is already the enforcement mechanism, so `-all` only adds risk of an SMTP-level bounce on relayed/forwarded mail evaluated before DKIM/DMARC even run, and `~all` is recommended instead |

Conditional mode resolves the *effective* policy the same way the DMARC
checker does — walking up to the parent for a subdomain with no record of
its own — so a subdomain correctly gets credited for `~all` once its parent
is enforcing, rather than being silently held to the strict default just
because it has no DMARC record of its own to check.

### Detected domains

Surfaces domain names seen in real report traffic that aren't registered yet,
so a subdomain sending mail under a registered parent's policy doesn't stay
invisible until something breaks (this is literally how a real incident got
diagnosed and fixed during development). Two things keep the list free of
noise:

- **Blocked-sender exclusion** — if every source IP behind a detected name is
  already reviewed and marked "blocked" for the domain it resolves under,
  it's confirmed spoofing/abuse, not a real subdomain worth registering, and
  it's excluded.
- **Dismiss** — an explicit "not mine" action for anything left over that
  still isn't worth registering (a lookalike, an unrecognized one-off
  sender), so it stops resurfacing.

### DNS checks run on a schedule

A background sweep (`run_dns_check_sweep`, every 15 minutes) re-checks every
*verified* domain whose last check is missing or older than 6 hours — not
tighter than that on purpose: several checks (STARTTLS in particular) open a
real SMTP connection to the domain's own mail servers, and DNS records don't
change often enough to justify repeating that every few minutes. A
newly-verified domain still gets its first check within one tick rather than
waiting the full 6 hours, since it has no prior check to compare against.
Unverified domains are never checked automatically — DNS ownership has to be
proven first (a TXT challenge at `_dmarc-dashboard-verify.<domain>`) so an
organization can't see check results for a domain they don't control.

### Domain rating

A single weighted score, out of 100, computed from the latest check results
plus observed DMARC pass rate over a rolling 90-day window:

| Factor | Weight |
|---|---|
| DMARC policy strength | 25 |
| DMARC pass rate (observed) | 20 |
| SPF | 10 |
| DKIM | 10 |
| MX | 10 |
| STARTTLS | 10 |
| MTA-STS | 5 |
| DANE | 5 |
| TLS-RPT | 5 |

`90+` is an A, `80+` a B, `70+` a C, `60+` a D, below that an F. Traffic from
sources explicitly reviewed and marked "blocked" is excluded from the pass
rate — confirmed abuse shouldn't drag a domain's score down forever. This
weighting is a first pass, meant to be checked against real domains and
adjusted, not treated as final.

## Production considerations

A single `api` + single `worker` on one well-specced VM is plenty for one
operator or a handful of orgs, and is the default. When you need more, both tiers
scale horizontally without a message broker — just Postgres:

- **Scaling the `worker`.** Background work runs off a Postgres work queue
  (`background_jobs`): the leader enqueues due jobs, and any number of workers
  claim them with `SELECT … FOR UPDATE SKIP LOCKED`, so they never
  double-process. Run more with `docker compose up -d --scale worker=3` — heavy
  report ingestion then parallelizes across replicas. Exactly one replica
  auto-elects itself **leader** (a Postgres advisory lock) to run the schedule;
  if it dies another takes over automatically. Each worker also serves a liveness
  endpoint (`:8080/health`) that reports unhealthy if its loop stalls, so an
  orchestrator recycles a wedged — not just crashed — replica.
- **Scaling the `api`.** It's stateless (sessions live in Postgres), so it scales
  out freely — with one caveat: the auth rate limiter defaults to in-memory
  per-process. Running multiple `api` replicas? Set **`RATE_LIMIT_BACKEND=postgres`**
  so the limit is shared and correct across them ([rate_limit.py](backend/app/services/auth/rate_limit.py));
  it's dependency-free (reuses Postgres) and the auth-endpoint volume is tiny.
- **No Redis, no Celery — on purpose.** Reusing the datastore you already run and
  have hardened (RLS, encryption-at-rest, backups) keeps the attack surface and
  the ops burden down versus adding a broker. It comfortably handles this
  workload's cadence; the queue is an internal abstraction that could move to a
  broker later if a genuinely high-throughput need appeared.
- **Connection pooling (PgBouncer).** Point `DATABASE_URL` at a PgBouncer
  instance instead of Postgres directly once replica count makes raw connection
  count matter — no other app changes needed in the general case (RLS's
  `SET LOCAL` context-setting is transaction-scoped, already compatible with
  transaction-mode pooling). The one exception: set `LEADER_DATABASE_URL` to a
  **direct, unpooled** Postgres connection string, since the leader's advisory
  lock is session-scoped and would misbehave under transaction pooling.
- **Read replicas for report/analytics queries.** Set `DATABASE_READ_URL` to a
  read-only replica connection string to offload the report/trend/posture
  dashboard endpoints there — unset by default, so this is entirely opt-in.
  Anything where you'd expect to see your own just-made write reflected
  immediately stays on the primary. **Known limitation:** three of these
  endpoints (report by-day/summary/grouped) can write to a small sender-identity
  cache on a cache miss; against a genuinely read-only replica that write fails.
  Don't point `DATABASE_READ_URL` at a genuinely read-only replica (a real
  streaming/hot standby, e.g. a managed Postgres read replica) until this is
  addressed — Postgres itself rejects that write on such a connection. What
  IS safe today: pointing it at a second connection to the *same primary*
  (e.g. for connection-count headroom via a second pooled route), since that
  connection accepts writes.
- **RLS binds a per-connection role, not a per-request identity.** Tenant
  isolation is enforced by `SET LOCAL` GUCs inside each request's transaction
  (see [Multi-tenancy](#multi-tenancy)); every app connection is the same
  `dmarc_app` role, so correctness depends on the app always setting org context —
  which is why the [RLS test suite](#tests) exists to keep that guarantee honest.
- **Still single by default: Postgres and the resolver.** One Postgres (use a
  managed, backed-up instance in production, or add a read replica per above)
  and one DNSSEC-validating Unbound `resolver`. Size the host, or use a managed
  database, as you grow. Postgres sharding (Citus/Hyperscale) is a deliberately
  deferred future option, not built here — the RLS-by-organization_id data
  model already gives it a natural shard key if a single primary's vertical
  ceiling is ever genuinely the bottleneck.

## Getting started

**Prerequisites**: Docker and Docker Compose, and something to terminate
TLS in front of this (nginx, Caddy, Nginx Proxy Manager, Traefik, ...) — the
app itself only speaks plain HTTP on `:8000`, deliberately: TLS termination
is a solved problem best left to a dedicated proxy rather than reimplemented
here. You'll also want a domain/subdomain pointed at this host to serve it
from (e.g. `dmarc.yourdomain.com`).

Two ways to deploy, same containers either way — the Docker Compose CLI, or
**[Portainer Stacks](#deploy-with-portainer-stacks)** if you manage Docker
through Portainer's web UI.

### Deploy with Docker Compose (CLI)

**1. Clone it**

```bash
git clone https://github.com/BJD1997/YetAnotherDmarcTool.git
cd YetAnotherDmarcTool
```

**2. Configure it**

```bash
cp .env.example .env
```

Open `.env` and fill in, at minimum:

- `PUBLIC_BASE_URL` — the HTTPS URL your reverse proxy will expose this at,
  e.g. `https://dmarc.yourdomain.com`. This has to be right before you sign
  in with Microsoft Entra SSO — it's part of the OAuth redirect URI.
- `POSTGRES_PASSWORD` and `DMARC_APP_DB_PASSWORD` — any two strong, distinct
  passwords.
- `FERNET_KEY` — 32 random bytes in URL-safe base64. Generate with either:
  ```bash
  openssl rand -base64 32 | tr '+/' '-_'                                    # no Python needed
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- `PLATFORM_ADMIN_BOOTSTRAP_EMAIL` / `PLATFORM_ADMIN_BOOTSTRAP_PASSWORD` —
  your own login for the very first account. Change the password after
  first login; these two vars can then be left as-is (bootstrap is a no-op
  once an admin already exists).

Most of what's left in `.env.example` is optional and individually
feature-gated — Entra SSO (for organization login), Cloudflare
auto-provisioning, security.txt: leave any of them blank to simply not
offer that feature rather than erroring. The one exception is
`ENTRA_MAIL_CLIENT_ID`/`ENTRA_MAIL_CLIENT_SECRET` (the "Mail Access" Entra
app) — both ways of actually ingesting reports, an organization connecting
its own mailbox *and* the operator-hosted mailbox, authenticate to
Microsoft Graph through this one shared app-only app registration. Leave
it unset and the dashboard still runs and does DNS best-practice checks
fine, but no report ever gets ingested by any path.

**3. Start it**

```bash
docker compose up -d
```

`migrate` runs first (schema migrations + bootstrapping the platform-admin
account from step 2), then `api` and `worker` start. Point your reverse
proxy's upstream at this host's `:8000`.

**4. Log in and set up your first organization**

Go to `https://<your-domain>/admin`, log in with the bootstrap credentials,
and create an organization. From there, see
[`docs/onboarding.md`](docs/onboarding.md) for the full walkthrough —
connecting a mailbox, verifying a domain, adding DKIM selectors, and what
"done" looks like.

### Deploy with Portainer (Stacks)

If you manage Docker through [Portainer](https://www.portainer.io/), use the
Portainer-tailored compose file —
[`docker-compose.portainer.yml`](docker-compose.portainer.yml) — with the
**Web editor** method. It's fully self-contained: it pulls the published image
(no build) and embeds the two files the CLI compose bind-mounts (the Postgres
init script for the RLS role, and the resolver config) directly in the YAML,
so there's nothing to clone.

> **Use the Web editor, not the Repository method.** With a Repository stack,
> Portainer only reads a `stack.env` committed *in the repo* — the variables
> you type in the UI are ignored, so `${...}` placeholders fall back to their
> defaults (which is why `API_HOST_PORT` wouldn't take and it kept trying to
> bind `:8000`). The Web editor builds the stack's environment from the
> variables you set, so they actually apply.

**1.** In Portainer: **Stacks → Add stack → Web editor**, and paste the
contents of `docker-compose.portainer.yml`.

**2.** Under **Environment variables**, add the settings below (they fill the
`${...}` placeholders in the compose file — the same options documented inline
in [`.env.example`](.env.example)):

| Variable | Required | Notes |
|---|---|---|
| `FERNET_KEY` | **Yes** | Encrypts TOTP secrets & stored credentials at rest. Generate one with:<br>`openssl rand -base64 32 \| tr '+/' '-_'` |
| `PUBLIC_BASE_URL` | **Yes** | Public HTTPS URL your reverse proxy serves this at, e.g. `https://dmarc.yourdomain.com` |
| `POSTGRES_PASSWORD` | **Yes** | Any strong password |
| `DMARC_APP_DB_PASSWORD` | **Yes** | A second, different strong password |
| `PLATFORM_ADMIN_BOOTSTRAP_EMAIL` / `PLATFORM_ADMIN_BOOTSTRAP_PASSWORD` | Recommended | Your first admin login (a no-op once an admin exists — change the password after first sign-in) |
| `FORWARDED_ALLOW_IPS` | Recommended | Your reverse proxy's IP, so real client IPs (not the proxy's) get logged |
| `IMAGE_TAG` | Optional | Image tag to run (default `latest`); pin to a release tag like `v0.1.2` if you prefer |
| `API_HOST_PORT` | Optional | Host port to publish (default `8000`); change if `8000` is taken |
| `ENTRA_MAIL_CLIENT_ID` / `ENTRA_MAIL_CLIENT_SECRET` | Optional | Report ingestion via Microsoft Graph — leave unset for DNS-checks-only |
| `ENTRA_SSO_CLIENT_ID` / `ENTRA_SSO_CLIENT_SECRET`, `CLOUDFLARE_*`, `HOSTED_REPORTS_*`, `SECURITY_CONTACT_EMAIL`, `MTA_STS_POLICY_*` | Optional | Feature-gated — leave unset to keep the feature off |

**3.** **Deploy the stack.** `migrate` runs first (schema migrations +
bootstrapping the admin account), then `api` and `worker` start. If `:8000`
is already in use on the host (e.g. another instance), set `API_HOST_PORT` to
a free port. Point your reverse proxy's upstream at this host's `:8000` (or
your `API_HOST_PORT`), exactly as in the CLI path.

**Updating:** change the `IMAGE_TAG` variable to the release you want and
redeploy the stack — Portainer re-pulls the image. (The in-app "Update now"
button is for the CLI deployment only; it isn't wired into the Portainer
path, which is why the `updater` service is omitted from this compose file.)

### Deploy on Azure (Container Apps)

For a managed, autoscaling cloud deployment — a private (VNet-integrated) Postgres,
a private Key Vault, and Container Apps that scale the api on HTTP concurrency and
the worker on the background-job queue depth, with a sizing-tier system (test/small/
medium/large) that pre-fills network, Postgres, and replica-count sizing:

> **Not yet functional:** the compiled `azuredeploy.json` the button needs hasn't
> been generated in this environment (no Azure CLI available), so clicking it will
> fail to resolve a template today. The **manual `az deployment group create`**
> path works right now, since it deploys straight from the Bicep source — see
> [`deploy/azure/README.md`](deploy/azure/README.md#manual-deploy-instead-of-the-button).
> The button also points at the `v0.1.5-beta` branch rather than `master` for now,
> since this work hasn't promoted to `master`/a stable tag yet; both of these will
> be revisited once `v0.1.5` goes stable.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.5-beta%2Fdeploy%2Fazure%2Fazuredeploy.json/createUIDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.5-beta%2Fdeploy%2Fazure%2FcreateUiDefinition.json)

The button opens a parameter form in the Azure Portal (admin credentials, a Fernet
key, optional Entra). It provisions everything and runs the database migration
automatically. Full details, prerequisites, the manual `az deployment` path, and
custom-domain setup are in [`deploy/azure/README.md`](deploy/azure/README.md). The
Bicep templates there are the source of truth; `azuredeploy.json` is compiled from them.

## Configuration

Every environment variable is documented inline in
[`.env.example`](.env.example) — copy it to `.env` and fill in what you need.
At minimum: `PUBLIC_BASE_URL` and the Postgres/Fernet secrets get the
dashboard itself running, with local email+password+TOTP login and DNS
best-practice checks. Entra SSO is genuinely optional on top of that (local
auth works with zero Microsoft-side configuration) — but report ingestion
itself isn't: both ways of getting reports in, an organization's own
connected mailbox and the operator-hosted mailbox, go through Microsoft
Graph via the same `ENTRA_MAIL_CLIENT_ID`/`ENTRA_MAIL_CLIENT_SECRET` app
registration, so without it you get DNS checks only, no ingested reports at
all. Cloudflare auto-provisioning and security.txt remain individually
feature-gated: unset, each one simply isn't offered rather than erroring.

## Development

```bash
docker compose build api worker      # after backend or frontend changes
docker compose run --rm migrate alembic upgrade head   # apply new migrations
docker compose up -d api worker
```

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

Covers the pure logic — the RFC-driven DNS checkers (DMARC subdomain
inheritance, SPF conditional mode, MTA-STS wildcard matching, DANE's DNSSEC
requirement), domain matching, and rating computation — mocking the DNS
layer rather than hitting real resolvers, so it's fast and deterministic.
Several of these tests exist specifically because the logic they cover had
a real bug found and fixed during development; they're regression tests as
much as documentation of the intended behavior.

**Row-level security** — the actual multi-tenancy boundary — has its own
integration suite (`backend/tests/test_rls.py`) that runs against a real
Postgres **as the non-owner `dmarc_app` role** (the one `FORCE ROW LEVEL
SECURITY` binds, and the one the app connects as), so it proves the same
isolation that protects production rather than mocking it: cross-tenant reads
are hidden, an unset org context fails closed, the platform-admin bypass works,
a cross-tenant write is refused by the policy's `WITH CHECK`, and a meta-guard
asserts **every** table with an `organization_id` column has `FORCE` RLS and a
policy — so a PR that adds a tenant-owned table but forgets its policy fails CI
instead of silently shipping a hole.

Those tests are gated on a `TEST_DATABASE_URL` env var; without it they skip, so
the default `pytest` run stays fast and needs no database. To run them locally:

```bash
docker run -d --name dmarc-test-db -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=dmarc_test -p 55432:5432 postgres:16-alpine
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:55432/dmarc_test pytest
```

CI (`.github/workflows/ci.yml`) sets `TEST_DATABASE_URL` against a Postgres
service container, so the RLS suite runs on every push/PR alongside the unit
tests and the frontend build. (A `postgres` service container is used rather
than `testcontainers-python` to avoid adding a Python test dependency — the
trade-off is that running the DB tests locally means starting the throwaway
Postgres yourself, as above.) Still not covered by automated tests: the full
report-ingestion pipeline and the HTTP routers — those are still verified by
exercising the deployed code path against real data.

New Alembic revisions go in `backend/alembic/versions/`, hand-written rather
than relying purely on autogenerate — see any existing migration for the
pattern (including the row-level-security policy that has to accompany any
new tenant-owned table).

Issues and PRs are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).
Found a security issue? See [`SECURITY.md`](SECURITY.md) rather than
opening a public issue.

## Roadmap

Rough direction, not promises:

- **IMAP report ingestion (top priority).** Today, mailbox-based report
  ingestion goes exclusively through Microsoft Graph (Entra app-only) — great if
  you're on Microsoft 365, but it turns away anyone on Google Workspace, a
  self-hosted Postfix/Dovecot mailbox, Fastmail, or any plain IMAP box. Adding a
  generic **IMAP** connector (app-password auth, plus OAuth2 for Google/Microsoft
  where available) would let this ingest from essentially any mailbox and is the
  single biggest lever for broader self-hosted adoption. DNS-checks-only use
  already needs no mailbox at all; this is about the report side.
- **Horizontal scale-out** — a real message queue and multiple workers, for the
  many-tenant / very-high-volume case that the current single in-process
  scheduler deliberately doesn't target (see [Production considerations](#production-considerations)).
- **Broader automated coverage** — extend the database-backed test suite (which
  now covers [RLS](#tests)) to the report-ingestion pipeline and HTTP routers.

## License

MIT — see [`LICENSE`](LICENSE). Permissive on purpose: usable, modifiable,
and rebrandable including commercially, as long as the copyright notice
stays intact.
