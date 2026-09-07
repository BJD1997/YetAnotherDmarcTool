# Repositories organization rule

One file per domain area, organized around the router/feature it primarily
serves rather than a strict mirror of `app/models/` (e.g. `repositories/domains.py`
for the `Domain` model, `repositories/dmarc_reports.py` for the queries behind
the `dmarc_reports` router, `repositories/selectors.py` for the `DkimSelector`
model, `repositories/source_identification.py` for the `SourceIpIdentity`
cache — deliberately its own file rather than folded into `dmarc_reports.py`,
since that table is a distinct, non-RLS-scoped concern shared across every
org, not something the `dmarc_reports` router owns). The rule is one canonical
home per query, organized by feature — not necessarily one file per model —
and routers and services call these functions rather than querying directly.

Every function takes `db: AsyncSession` as its first argument and returns data
(or raises the standard `HTTPException` for an ownership/not-found check, e.g.
`domains.get_owned_domain`). No module-level caches, singletons, or other
process-local state — the API must stay safe to run as N stateless replicas
(see the horizontal-scale-out work on `v0.1.4-beta`).
