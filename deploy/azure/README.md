# Deploy on Azure Container Apps

One-click, autoscaling deployment of YetAnotherDmarcTool on **Azure Container Apps**,
with a **private (VNet-integrated) PostgreSQL Flexible Server**, a **private
(VNet-integrated) Azure Key Vault**, and a **sizing-tier system** (test/small/medium/large)
that pre-fills network, Postgres, and replica-count sizing. Bicep is the source of
truth (`main.bicep` + `modules/`); `azuredeploy.json` is the compiled ARM the Portal
button uses.

> **Keeping `azuredeploy.json` in sync:** the button loads the compiled ARM
> template, which is not regenerated automatically. After any change to
> `main.bicep` or a module, regenerate and commit it, from `deploy/azure/`:
> ```bash
> az bicep build --file main.bicep --outfile azuredeploy.json
> # or with the standalone Bicep CLI:
> # bicep build main.bicep --outfile azuredeploy.json
> ```

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fmain%2Fdeploy%2Fazure%2Fazuredeploy.json/createUIDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fmain%2Fdeploy%2Fazure%2FcreateUiDefinition.json)

## What it creates
- **VNet** with three subnets — one delegated to the Container Apps environment, one
  delegated to the Postgres Flexible Server, and one (non-delegated) for the Key
  Vault private endpoint — plus `privatelink.postgres.database.azure.com` and
  `privatelink.vaultcore.azure.net` private DNS zones, both linked to the VNet. CIDR
  ranges scale with the deployment size (below).
- **Azure Database for PostgreSQL Flexible Server** — private access only (no public
  endpoint), SKU/storage/HA set as described below.
- **Key Vault, private-endpoint-only** (`publicNetworkAccess: Disabled`, RBAC
  authorization, no public network path) — plus a **user-assigned managed identity**
  the apps use to read secrets via Key Vault references, resolved over the VNet
  through the private DNS zone above.
- **A sizing-tier system** (`deploymentSize`: `test` / `small` / `medium` / `large`) —
  one parameter pre-fills VNet/subnet CIDRs, the Postgres SKU + storage, and the
  api/worker replica ceilings from a lookup table in `main.bicep`. Every tier-derived
  value stays individually overridable (`postgresSkuNameOverride`,
  `apiMaxReplicasOverride`, etc. — see Parameters below) without having to fork the
  template.
- **Postgres high availability** (`postgresHighAvailability`: `Disabled` /
  `ZoneRedundant`) — a separate, independent toggle from the sizing tier: a
  zone-redundant standby with automatic failover, at roughly double the Postgres
  compute cost. Off by default at every tier.
- **Container Apps environment** (VNet-injected) + **Log Analytics** (retention
  scales with the deployment size, 30-90 days).
- **api** app — external HTTPS ingress, autoscales on HTTP concurrency; **worker**
  app — internal, autoscales on the background-job queue depth (KEDA Postgres
  scaler, min 1 so a leader always exists). Both replica ceilings come from the
  sizing tier. Each runs a **DNSSEC Unbound resolver sidecar** (ACA has no UDP
  ingress, so DNS is over `127.0.0.1`).
- **migrate job** — creates or updates the non-owner `dmarc_app` role (its
  password included), runs Alembic, bootstraps the platform admin. A
  `deploymentScript` runs it during deployment, before the apps start; the
  updater job runs it again on every update.
- **updater job** — started by **Update now** in the admin console; runs the
  migrations, then moves the worker, api and itself to the new release. It has
  its own managed identity with a custom role limited to this resource group's
  container apps and jobs; the api has a second identity that may only start
  this job. See [Updating](#updating).

## Prerequisites
- An Azure subscription, and **Owner** (or **Contributor + User Access Administrator**) on the target resource group. The template creates **custom roles** (the updater job's role, and the api's "may only start the updater" role) and **role assignments** (Key Vault access for the app identity, Contributor for the migrate deployment script, the two custom roles).
- These resource providers registered (Portal usually auto-registers; via CLI: `az provider register -n <NS>`): `Microsoft.App`, `Microsoft.DBforPostgreSQL`, `Microsoft.KeyVault`, `Microsoft.OperationalInsights`, `Microsoft.ManagedIdentity`, `Microsoft.ContainerInstance` (used by the deployment script), `Microsoft.Network`.
- A **Fernet key** for `fernetKey`: `openssl rand -base64 32 | tr '+/' '-_'`.

## Parameters (the ones you'll set)
| Parameter | Required | Notes |
|---|---|---|
| `namePrefix` | optional (default `yadt`) | 2–12 chars, lowercase-letter first. Prefix for resource names. |
| `imageTag` | optional (default `latest`) | Release tag (e.g. `v0.1.6`) or `latest`. Drives the app, updater and resolver sidecar images. After the first deployment, **Update now** moves to newer releases; this only matters again when you redeploy. |
| `deploymentSize` | optional (default `small`) | `test` / `small` / `medium` / `large`. Pre-fills network, Postgres, and replica-count sizing — see "What it creates" above. |
| `postgresHighAvailability` | optional (default `Disabled`) | `Disabled` / `ZoneRedundant`. Independent of `deploymentSize` — roughly doubles Postgres compute cost when enabled. Not offered (hidden) in the wizard for `test`/`small`: Azure's Burstable Postgres SKU (both tiers) doesn't support zone-redundant HA at all — only General Purpose/Memory Optimized (`medium`/`large`) do. Also requires a region with Availability Zone support; an incompatible region surfaces as an Azure deployment-time validation error, not a wizard warning. |
| `administratorLogin` / `administratorPassword` | password required (login defaults to `dmarcadmin`) | Postgres admin, used only by the migrate job. Azure's Postgres admin is not a superuser; the migrate job is written for that. |
| `dmarcAppDbPassword` | yes | Password for the non-owner `dmarc_app` role the app connects as. To change it, redeploy with a new value: the migrate job updates the role and the apps get the new connection string. |
| `fernetKey` | yes | Encrypts TOTP secrets/credentials at rest. |
| `platformAdminBootstrapEmail` / `…Password` | recommended | First platform-admin login, created if none exists. |
| `entra*` | optional | Entra SSO (api) and/or Graph mailbox ingestion (worker). Leave blank for local auth + DNS-checks-only. |
| `hostedReportsTenantId` / `hostedReportsMailboxAddress` | optional | One mailbox in your own Microsoft 365 tenant that gives each domain its own `mailbox+tag@<mailbox's domain>` reporting address. Needs the Entra Mail client. Both or neither. |
| `cloudflareZoneId` / `cloudflareApiToken` | optional | Needs the hosted mailbox. Creates and removes the DMARC authorization record for each hosted address in that domain's Cloudflare zone. Without it, you add those records by hand. Use a token limited to DNS edit on that zone. |
| `enableTestUpdate` | optional (default `false`) | Shows **Run test update** in the admin console (see [Updating](#updating)). Not in the portal wizard; set it in a parameters file, or set `UPDATE_REHEARSAL_ENABLED` on the api app. |
| `enableStarttlsCheck` | optional (default `false`) | Runs the STARTTLS check, which connects to each MX host on port 25. Azure blocks outbound port 25 for every subscription type except Enterprise Agreement, so it's off by default: otherwise every domain gets a STARTTLS error and a lower grade. Not in the portal wizard. |
| `publicBaseUrlOverride` | optional | A custom domain URL; leave blank to use the default ACA URL. |
| `postgresSkuNameOverride` / `postgresSkuTierOverride` / `postgresStorageGBOverride` | optional | Override the tier-derived Postgres SKU/storage. Empty/`0` = use the `deploymentSize` default. |
| `apiMaxReplicasOverride` / `workerMaxReplicasOverride` | optional | Override the tier-derived autoscale ceilings. `0` = use the `deploymentSize` default. |

## After deployment
1. **Confirm the migrate job succeeded** — the deployment fails if it didn't. Manually: `az containerapp job execution list -n <namePrefix>-migrate -g <rg> -o table`.
2. **Open the app** — the deployment output `apiUrl` is the HTTPS URL; `/api/health` should return 200. Log in with the bootstrap admin.
3. **Check the worker** — `az containerapp logs show -n <namePrefix>-worker -g <rg>` should show a leader elected and jobs draining; `SELECT status, count(*) FROM background_jobs GROUP BY 1;` on the DB confirms processing.

### Custom domain + managed TLS (post-deploy, optional)
The button deploys on the default `*.azurecontainerapps.io` domain with automatic TLS. To use your own domain (cert binding needs DNS records that only exist once the app does):
1. Add the CNAME + `asuid.` TXT validation records at your DNS provider pointing at the api FQDN.
2. `az containerapp hostname add` then `az containerapp hostname bind --environment <env> --validation-method CNAME` (issues a free **ACA managed certificate**).
3. Redeploy with `publicBaseUrlOverride=https://your.domain` (or `az containerapp update --set-env-vars PUBLIC_BASE_URL=…`) so cookies/HSTS use the real URL.

### Updating
**From the admin console:** Updates → **Update now**, the same as on Docker
Compose. It starts the `<namePrefix>-updater` job, which runs the database
migrations and then moves the worker, the api (with its resolver sidecar) and
itself to the new release. The page shows the new version when it's done.

How it's locked down:
- The api's identity (`<namePrefix>-update-trigger-id`) may only **start**
  the updater job, with no overrides.
- The job reads the requested release back from the api and refuses anything
  that isn't a real release tag newer than what's running.
- The job's identity (`<namePrefix>-updater-id`) has a custom role limited to
  this resource group's container apps and jobs: read, update, start, and read
  job runs. No delete, no exec, no assigning identities.

**Run test update** (same page, Azure only) runs every one of those steps on
the version you already run, so you can check the permissions before a real
update. Nothing changes; the app restarts briefly. It's hidden unless turned
on: redeploy with `enableTestUpdate=true`, or set `UPDATE_REHEARSAL_ENABLED`
to `true` on the `<namePrefix>-api` container app (Containers → Environment
variables). Turn it off again afterwards.

Creating those custom roles needs **Owner** or **User Access Administrator**
on the resource group at deployment time. Role assignments can take a few
minutes to apply after a fresh deployment. If the first **Update now** is
refused, try again shortly.

A failed update is reported in the updater job's run history (Azure Portal →
`<namePrefix>-updater` → Execution history → logs). Migrations run first, so
the app itself is only changed after they succeed.

**By redeploying:** re-run the deployment (button or CLI) with a new
`imageTag` and the same parameters as before. This also applies template
changes, not just the new images.

## Manual deploy (instead of the button)
```bash
az group create -n <rg> -l <region>
cp deploy/azure/main.parameters.example.json my.parameters.json   # fill in secrets
az deployment group what-if -g <rg> -f deploy/azure/main.bicep -p @my.parameters.json   # preview
az deployment group create   -g <rg> -f deploy/azure/main.bicep -p @my.parameters.json
```
If the in-deployment migrate step ever doesn't run, trigger it yourself:
`az containerapp job start -n <namePrefix>-migrate -g <rg>`.

This path deploys directly from `main.bicep`, no compiled ARM template needed, and
`what-if` gives you a preview before anything is created.

## Notes & trade-offs
- **Sizing tiers are a starting point, not a ceiling.** `deploymentSize` picks
  sensible defaults for network/Postgres/replica sizing per tier; every one of
  those values is individually overridable (see Parameters) without editing the
  template. Pick the closest tier and override only what you need to change.
- **Postgres high availability costs roughly double.** `postgresHighAvailability=ZoneRedundant`
  adds a synchronously-replicated standby in a second zone with automatic
  failover — worthwhile for production, but it's an explicit opt-in for a reason:
  it roughly doubles the Postgres compute cost over the same SKU without it.
  Two hard constraints to know before overriding it via CLI/parameters file
  (the wizard already enforces the first by hiding the checkbox): it requires
  a `GeneralPurpose`/`MemoryOptimized` Postgres SKU — Azure does not support it
  on `Burstable` (`test`/`small`'s default SKU) at all — and it requires a
  region with Availability Zone support; an incompatible region surfaces as an
  Azure deployment-time validation error, not a pre-flight warning.
- **Redeploying into a resource group you deleted can collide on the Key Vault
  name.** The vault's name is derived from `uniqueString(resourceGroup().id)`,
  which is stable for the same resource-group name/subscription, and soft-delete
  keeps a deleted vault's name reserved for 7 days. If a redeploy within that
  window fails with "vault name already in use," either wait it out or run
  `az keyvault purge -n <vaultName>` first (irreversible — only do this if
  nothing needs recovering from the deleted vault).
- **Cost (baseline, `small` tier, no HA)**: Container Apps Consumption
  (scale-to-1 here for the always-on leader), a Burstable `Standard_B2s`
  Postgres, Key Vault, and Log Analytics — a small always-on baseline. Raise the
  deployment size / Postgres SKU / replica ceilings for real load; enable HA for
  production.
- **Tested on a live subscription:** deployed, then updated in place with **Update now** (v0.1.5 release candidates). `az deployment group what-if` is still the way to preview a change to an existing deployment.
