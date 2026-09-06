# Services organization rule

A service becomes its own subpackage (`services/<name>/` with multiple files) once
it needs 2+ files to express — e.g. `dns_checks/` (18 files), `auth/`, `rating/`.

A service that's genuinely one file's worth of logic stays flat at `services/`
root — e.g. `dmarc_analytics.py`, `dmarc_narrative.py`, `update_check.py`,
`updater_client.py`. Don't split a flat file into a subpackage just to "match"
the others; only graduate it when it actually grows a second file.
