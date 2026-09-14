# YetAnotherDmarcTool

[![CI](https://github.com/BJD1997/YetAnotherDmarcTool/workflows/CI/badge.svg)](https://github.com/BJD1997/YetAnotherDmarcTool/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docs: Wiki](https://img.shields.io/badge/docs-wiki-blue.svg)](https://github.com/BJD1997/YetAnotherDmarcTool/wiki)
[![Live Demo](https://img.shields.io/badge/demo-live-brightgreen.svg)](https://demo.yetanotherdmarctool.com)

**Self-hosted, multi-tenant DMARC & TLS-RPT reporting dashboard.** DNS
best-practice checks, a 0–100 domain rating, and a guided path to
`p=reject` — read-only and advisory toward every domain it watches.

## Live demo

**[demo.yetanotherdmarctool.com](https://demo.yetanotherdmarctool.com)** — live checks against this project's own domain.
```
email:    demo@yetanotherdmarctool.com
password: lantern-maple-falcon-willow-989
```

## ✨ Why YADT

- 🔍 DMARC aggregate/forensic + TLS-RPT reports, day-grouped, per-record drill-down
- ✅ SPF · DKIM · DMARC · DMARCbis · MX · MTA-STS · DANE · STARTTLS · TLS-RPT, checked continuously
- 📊 One 0–100 rating synthesizing every check + observed pass rate
- 🧭 Guided policy builder — only recommends tightening `p=` once it's safe
- 🕵️ Surfaces unregistered subdomains & DKIM selectors seen in real traffic
- 🏢 Multi-tenant enforced at the database layer (Postgres RLS)
- 🔑 Microsoft Entra SSO or local email + password + TOTP, chosen per organization

→ **[Core Concepts](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Core-Concepts)** for how each of these works.

## 🚀 Quick start

```bash
git clone https://github.com/BJD1997/YetAnotherDmarcTool.git && cd YetAnotherDmarcTool
cp .env.example .env   # fill in PUBLIC_BASE_URL, 2 passwords, FERNET_KEY
docker compose up -d
```

Open `https://<your-domain>/admin`, log in with your bootstrap credentials, create your first org.

→ **[Getting Started](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Getting-Started)** (Portainer, Azure, full config reference) · **[Onboarding](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Onboarding-Your-First-Organization)**

## ☁️ Deploy on Azure

A managed, autoscaling deployment (private Postgres + Key Vault, KEDA-scaled Container Apps, a Test/Small/Medium/Large sizing picker) — this branch's own addition, not yet on `master`.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.5-beta%2Fdeploy%2Fazure%2Fazuredeploy.json/createUIDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.5-beta%2Fdeploy%2Fazure%2FcreateUiDefinition.json)

Compiled and cross-checked (`bicep build` clean, 0 errors/warnings) but not yet tested against a live subscription — the manual `az deployment group create` path is the fallback if anything doesn't resolve as expected. Full parameters, prerequisites, and that manual path → **[`deploy/azure/README.md`](deploy/azure/README.md)** · narrative overview → **[Deploying on Azure Container Apps](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Deploying-on-Azure-Container-Apps)**.

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
                 └────────────┘  sweep, retention purge — no message broker
```

`api`/`worker` are the same image, different commands; `migrate` runs
Alembic + bootstraps the first admin before either starts.

→ **[Architecture & Internals](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Architecture-and-Internals)** for the full tech stack + licenses.

## 📚 Documentation

| | |
|---|---|
| 🚀 [Getting Started](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Getting-Started) | Deploy (Compose/Portainer/Azure), config reference, onboarding |
| 🧠 [Core Concepts](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Core-Concepts) | RLS, DMARC inheritance, report attribution, SPF modes, rating |
| 🏗️ [Architecture & Internals](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Architecture-and-Internals) | System diagram, tech stack, licenses |
| 📈 [Production & Scaling](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Production-and-Scaling) | Horizontal scale-out, PgBouncer, read replicas, releases |
| 🩹 [Troubleshooting & FAQ](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Troubleshooting-and-FAQ) | Common issues and how to fix them |
| 🤝 [Contributing](CONTRIBUTING.md) | Dev setup, tests, code style |
| 🗺️ [Roadmap](https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Roadmap) | What's next |

Security issue? [`SECURITY.md`](SECURITY.md) — please don't open a public issue.

## About

Built as a hobby project — wanted a self-hosted tool that covered all of
this in one place, couldn't find one, so built it (with Claude's help).
Multi-tenant because that's an MSP habit: anything worth building gets
built to support more than one tenant, even at a scale of one.

## License

MIT — see [`LICENSE`](LICENSE).
