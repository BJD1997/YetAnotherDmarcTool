# YetAnotherDmarcTool

[![CI](https://github.com/BJD1997/YetAnotherDmarcTool/workflows/CI/badge.svg)](https://github.com/BJD1997/YetAnotherDmarcTool/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs: Wiki](https://img.shields.io/badge/docs-wiki-blue.svg)](https://github.com/BJD1997/YetAnotherDmarcTool/wiki)
[![Live Demo](https://img.shields.io/badge/demo-live-brightgreen.svg)](https://demo.yetanotherdmarctool.com)

**Self-hosted, multi-tenant DMARC & TLS-RPT reporting dashboard.** DNS
best-practice checks, a 0–100 domain rating, and a guided path to
`p=reject` — in one place.

Built as a hobby project: I wanted a self-hosted tool that actually covered
all of this, couldn't find one, so built it (with Claude's help). It's not
run as a commercial service — multi-tenant is just an MSP habit: after years
of that mindset, anything worth building gets built as if it should support
more than one tenant, even at a scale of one.

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

## ✨ Why YADT?

- 🔍 **See everything** — DMARC aggregate + forensic reports and TLS-RPT
  reports, day-grouped with per-record drill-down (source IP, disposition,
  SPF/DKIM alignment, plain-English pass/fail narratives).
- ✅ **DNS best practice, checked continuously** — SPF, DKIM, DMARC,
  DMARCbis (RFC 9989), MX, MTA-STS, DANE, STARTTLS, TLS-RPT, run on a
  schedule or on demand.
- 📊 **One score, 0–100** — synthesizes every check result plus observed
  DMARC pass rate into a single rating + letter grade.
- 🧭 **A guided policy builder** — DNS record generators and a rollout
  recommendation engine that only suggests tightening `p=` once pass rate,
  stability, and sender review are actually there.
- 🕵️ **Sender inventory & detected domains** — identifies what's really
  sending mail as a domain, and proactively surfaces subdomains/DKIM
  selectors seen in real traffic that were never registered.
- 🏢 **Multi-tenant at the database layer** — Postgres row-level security,
  not an application-code convention.
- 🔑 **Two auth paths** — Microsoft Entra SSO for Microsoft 365
  organizations, local email + password + TOTP for everyone else, chosen
  automatically per organization.

Full detail on how each of these actually works → **[Core Concepts](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Core-Concepts)**.

## 🚀 Quick start

```bash
git clone https://github.com/BJD1997/YetAnotherDmarcTool.git && cd YetAnotherDmarcTool
cp .env.example .env   # fill in PUBLIC_BASE_URL, 2 passwords, FERNET_KEY
docker compose up -d
```

Then open `https://<your-domain>/admin` and log in with your bootstrap
credentials to create your first organization.

📖 **Full walkthrough** (Portainer, Azure Container Apps, every config
option) → **[Getting Started](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Getting-Started)**
📖 **Setting up your first organization** → **[Onboarding guide](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Onboarding-Your-First-Organization)**

## 🏗️ Architecture

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

The RFC-driven rules behind DMARC subdomain inheritance, report attribution,
SPF's conditional mode, detected domains, DNS check scheduling, and the
domain rating formula are documented, with worked examples, in
**[Core Concepts](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Core-Concepts)**
— and briefly, RLS-first, here:

Every tenant-owned table has Postgres row-level security enabled and
*forced* (`FORCE ROW LEVEL SECURITY` — without this, the table's own owner
role bypasses RLS regardless of the policy). The app connects as a separate,
non-owner `dmarc_app` role specifically so RLS actually applies to it. Each
request sets `app.current_org_id` via `SET LOCAL` for the duration of one
transaction; a small number of cross-org background jobs (the DNS check
sweep, domain verification sweep, retention purge) instead set
`app.is_platform_admin = true`, which every policy also accepts. Full detail
→ **[Multi-Tenancy & Row-Level Security](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Multi-Tenancy-and-Row-Level-Security)**.

## Production considerations

A single `api` + single `worker` on one well-specced VM is plenty for one
operator or a handful of orgs, and is the default. When you need more, both
tiers scale horizontally without a message broker — just Postgres. Worker
scale-out (`docker compose up -d --scale worker=3`), leader election,
PgBouncer, and read-replica support are all covered in
**[Scaling Horizontally](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Scaling-Horizontally)**.

## Getting started

See **[Getting Started](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Getting-Started)**
in the wiki for the full walkthrough — Docker Compose, Portainer Stacks,
Azure Container Apps, and the complete configuration reference — or the
[Quick start](#-quick-start) above for the fastest path.

## Configuration

Every environment variable is documented inline in
[`.env.example`](.env.example) and in the wiki's
**[Configuration Reference](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Configuration-Reference)**.
At minimum: `PUBLIC_BASE_URL` and the Postgres/Fernet secrets get the
dashboard itself running, with local email+password+TOTP login and DNS
best-practice checks. Entra SSO is genuinely optional on top of that — but
report ingestion itself isn't: both ways of getting reports in go through
the same `ENTRA_MAIL_CLIENT_ID`/`ENTRA_MAIL_CLIENT_SECRET` app registration,
so without it you get DNS checks only, no ingested reports at all.

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
isolation that protects production rather than mocking it. Those tests are
gated on a `TEST_DATABASE_URL` env var; without it they skip, so the default
`pytest` run stays fast and needs no database. Full setup instructions and
what CI covers → **[Running the Test Suite](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Running-the-Test-Suite)**.

Issues and PRs are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).
Found a security issue? See [`SECURITY.md`](SECURITY.md) rather than
opening a public issue.

## 📚 Full documentation

| | |
|---|---|
| 🚀 [Getting Started](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Getting-Started) | Quick start, Portainer, Azure, full config reference, onboarding |
| 🧠 [Core Concepts](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Core-Concepts) | RLS, DMARC inheritance, report attribution, SPF modes, domain rating |
| 🏗️ [Architecture & Internals](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Architecture-and-Internals) | System diagram, tech stack, licenses |
| 📈 [Production & Scaling](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Production-and-Scaling) | Horizontal scale-out, PgBouncer, read replicas, releases |
| 🩹 [Troubleshooting & FAQ](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Troubleshooting-and-FAQ) | Common issues and how to fix them |
| 🤝 [Contributing & Development](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Contributing-and-Development) | Dev setup, tests, code style |
| 🗺️ [Roadmap](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Roadmap) | What's next, roughly |

## License

MIT — see [`LICENSE`](LICENSE). Permissive on purpose: usable, modifiable,
and rebrandable including commercially, as long as the copyright notice
stays intact.
