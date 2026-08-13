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
                 │   worker   │  APScheduler: mailbox polling, DNS check
                 │            │  sweep, domain verification sweep, retention
                 └────────────┘  purge — all cron-style, no message queue
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
| `apscheduler` | MIT | In-process cron-style scheduling for the `worker` service |
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
- `FERNET_KEY` — generate with:
  ```bash
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

If you manage Docker through [Portainer](https://www.portainer.io/), deploy
the Portainer-tailored compose file —
[`docker-compose.portainer.yml`](docker-compose.portainer.yml) — as a
**Repository** stack. Use the Repository method, *not* the web editor: the
stack needs files from this repo (the Postgres init script that creates the
RLS role, and the resolver config), which only the git clone brings along.
That compose pulls the published image instead of building, and takes all its
settings from environment variables you set in Portainer's UI instead of a
`.env` file.

**1.** In Portainer: **Stacks → Add stack → Repository**.

**2.** Point it at this repo:
- **Repository URL**: `https://github.com/BJD1997/YetAnotherDmarcTool`
- **Reference**: `refs/heads/master` (or a release tag, e.g. `refs/tags/v0.1.2`)
- **Compose path**: `docker-compose.portainer.yml`

**3.** Under **Environment variables**, add the settings below (they fill the
`${...}` placeholders in the compose file — the same options documented inline
in [`.env.example`](.env.example)):

| Variable | Required | Notes |
|---|---|---|
| `FERNET_KEY` | **Yes** | Encrypts TOTP secrets & stored credentials at rest. Generate one with:<br>`docker run --rm ghcr.io/bjd1997/yetanotherdmarctool:latest python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `PUBLIC_BASE_URL` | **Yes** | Public HTTPS URL your reverse proxy serves this at, e.g. `https://dmarc.yourdomain.com` |
| `POSTGRES_PASSWORD` | **Yes** | Any strong password |
| `DMARC_APP_DB_PASSWORD` | **Yes** | A second, different strong password |
| `PLATFORM_ADMIN_BOOTSTRAP_EMAIL` / `PLATFORM_ADMIN_BOOTSTRAP_PASSWORD` | Recommended | Your first admin login (a no-op once an admin exists — change the password after first sign-in) |
| `FORWARDED_ALLOW_IPS` | Recommended | Your reverse proxy's IP, so real client IPs (not the proxy's) get logged |
| `APP_VERSION` | Optional | Image tag to run (default `latest`); pin to a release tag like `v0.1.2` if you prefer |
| `API_HOST_PORT` | Optional | Host port to publish (default `8000`); change if `8000` is taken |
| `ENTRA_MAIL_CLIENT_ID` / `ENTRA_MAIL_CLIENT_SECRET` | Optional | Report ingestion via Microsoft Graph — leave unset for DNS-checks-only |
| `ENTRA_SSO_CLIENT_ID` / `ENTRA_SSO_CLIENT_SECRET`, `CLOUDFLARE_*`, `HOSTED_REPORTS_*`, `SECURITY_CONTACT_EMAIL`, `MTA_STS_POLICY_*` | Optional | Feature-gated — leave unset to keep the feature off |

**4.** **Deploy the stack.** `migrate` runs first (schema migrations +
bootstrapping the admin account), then `api` and `worker` start. Point your
reverse proxy's upstream at this host's `:8000` (or your `API_HOST_PORT`),
exactly as in the CLI path.

**Updating:** change the `APP_VERSION` variable to the release you want and
redeploy the stack — Portainer re-pulls the image. (The in-app "Update now"
button is for the CLI deployment only; it isn't wired into the Portainer
path, which is why the `updater` service is omitted from this compose file.)

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

Doesn't yet cover anything that needs a real database (report ingestion,
the routers, RLS) — for that, verification is still done by rebuilding,
redeploying, and exercising the actual code path against real data (a real
domain's real DNS, a real report already ingested), same as it always has
been. CI (`.github/workflows/ci.yml`) runs the test suite and the frontend
build on every push/PR.

New Alembic revisions go in `backend/alembic/versions/`, hand-written rather
than relying purely on autogenerate — see any existing migration for the
pattern (including the row-level-security policy that has to accompany any
new tenant-owned table).

Issues and PRs are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).
Found a security issue? See [`SECURITY.md`](SECURITY.md) rather than
opening a public issue.

## License

MIT — see [`LICENSE`](LICENSE). Permissive on purpose: usable, modifiable,
and rebrandable including commercially, as long as the copyright notice
stays intact.
