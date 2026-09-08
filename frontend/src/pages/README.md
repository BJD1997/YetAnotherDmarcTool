# Pages organization rule

A standalone route stays directly under `pages/`. Once a feature has two or
more related route components, group them in a feature directory:

- `pages/admin/` owns the platform-administration routes.
- `pages/domain-detail/` owns the nested `/domains/:domainId/*` routes.
- `pages/settings/` owns the nested `/settings/*` routes.

Reusable UI belongs under `components/`, not here. A helper that is meaningful
only to one route may stay beside that route; move it only when it gains another
consumer. File length alone is not a reason to split a cohesive screen—extract
when a section has its own data/state contract or is genuinely reusable.
