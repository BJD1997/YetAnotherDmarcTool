# Deploy on Azure Container Apps

One-click, autoscaling deployment of YetAnotherDmarcTool on **Azure Container Apps**,
with a **private (VNet-integrated) PostgreSQL Flexible Server**, a **private
(VNet-integrated) Azure Key Vault**, and a **sizing-tier system** (test/small/medium/large)
that pre-fills network, Postgres, and replica-count sizing. Bicep is the source of
truth (`main.bicep` + `modules/`); `azuredeploy.json` is the compiled ARM the Portal
button uses.

> **The button below is not yet functional.** `azuredeploy.json` (the compiled ARM
> template the Portal loads) hasn't been generated yet — this environment has neither
> the Azure CLI nor the Bicep CLI available to run the compile step. Clicking the
> button today will fail to resolve a template.
>
> **Use the [manual deploy](#manual-deploy-instead-of-the-button) path below instead** —
> it deploys straight from the Bicep source and needs no compiled JSON at all, so it
> works right now.
>
> To make the button work: someone with the Azure CLI installed needs to run, from
> the repo root:
> ```bash
> cd deploy/azure && az bicep build --file main.bicep --outfile azuredeploy.json
> ```
> and commit the resulting `azuredeploy.json`. A good follow-up (out of scope for this
> change) would be to automate that compile step in CI, so the committed artifact can
> never drift out of sync with `main.bicep` — it isn't wired up yet.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.5-beta%2Fdeploy%2Fazure%2Fazuredeploy.json/createUIDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.5-beta%2Fdeploy%2Fazure%2FcreateUiDefinition.json)

> The button points at the `v0.1.5-beta` branch, not `master` — this work (the
> sizing-tier system, the Key Vault private endpoint, the resolver sidecar image) lives
> on `v0.1.5-beta` and hasn't been promoted to `master`/a stable tag yet. Repoint it to
> `master` (or a stable `v0.1.5` tag) once `v0.1.5` promotes to stable, mirroring how
> `imageTag`'s own default matured over this project's beta cycle
> (`v0.1.4-beta1` → `v0.1.4-rc*` → `v0.1.4` stable).

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
- **migrate job** — creates the non-owner `dmarc_app` role, runs Alembic, bootstraps
  the platform admin. A `deploymentScript` runs it during deployment, before the apps
  start.

## Prerequisites
- An Azure subscription, and **Owner** (or **Contributor + User Access Administrator**) on the target resource group — the template creates **role assignments** (Key Vault access for the app identity; Contributor for the migrate deployment script).
- These resource providers registered (Portal usually auto-registers; via CLI: `az provider register -n <NS>`): `Microsoft.App`, `Microsoft.DBforPostgreSQL`, `Microsoft.KeyVault`, `Microsoft.OperationalInsights`, `Microsoft.ManagedIdentity`, `Microsoft.ContainerInstance` (used by the deployment script), `Microsoft.Network`.
- A **Fernet key** for `fernetKey`: `openssl rand -base64 32 | tr '+/' '-_'`.

## Parameters (the ones you'll set)
| Parameter | Required | Notes |
|---|---|---|
| `namePrefix` | yes | 2–12 chars, lowercase-letter first. Prefix for resource names. |
| `imageTag` | yes | Release tag (e.g. `v0.1.5`) or `latest`. Drives both the app image and the resolver sidecar image. |
| `deploymentSize` | yes | `test` / `small` (default) / `medium` / `large`. Pre-fills network, Postgres, and replica-count sizing — see "What it creates" above. |
| `postgresHighAvailability` | yes | `Disabled` (default) / `ZoneRedundant`. Independent of `deploymentSize` — roughly doubles Postgres compute cost when enabled. Not offered (hidden) in the wizard for `test`/`small`: Azure's Burstable Postgres SKU (both tiers) doesn't support zone-redundant HA at all — only General Purpose/Memory Optimized (`medium`/`large`) do. Also requires a region with Availability Zone support; an incompatible region surfaces as an Azure deployment-time validation error, not a wizard warning. |
| `administratorLogin` / `administratorPassword` | yes | Postgres admin (used only by the migrate job). |
| `dmarcAppDbPassword` | yes | Password for the non-owner `dmarc_app` role the app connects as. |
| `fernetKey` | yes | Encrypts TOTP secrets/credentials at rest. |
| `platformAdminBootstrapEmail` / `…Password` | recommended | First platform-admin login, created if none exists. |
| `entra*` | optional | Entra SSO (api) and/or Graph mailbox ingestion (worker). Leave blank for local auth + DNS-checks-only. |
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
Re-run the deployment (button or CLI) with a new `imageTag` — this updates the app
image and the resolver sidecar image together. To update manually instead:
`az containerapp update -n <namePrefix>-api -g <rg> --container-name api --image ghcr.io/bjd1997/yetanotherdmarctool:<tag>`
(and the same `--container-name worker` for `-worker`); update the `resolver`
container name the same way if you also need to move the resolver image forward on
its own. If a release adds migrations, run the migrate job again: `az containerapp job start -n <namePrefix>-migrate -g <rg>`.

## Manual deploy (instead of the button)
```bash
az group create -n <rg> -l <region>
cp deploy/azure/main.parameters.example.json my.parameters.json   # fill in secrets
az deployment group what-if -g <rg> -f deploy/azure/main.bicep -p @my.parameters.json   # preview
az deployment group create   -g <rg> -f deploy/azure/main.bicep -p @my.parameters.json
```
If the in-deployment migrate step ever doesn't run, trigger it yourself:
`az containerapp job start -n <namePrefix>-migrate -g <rg>`.

This path works today regardless of the `azuredeploy.json` gap above — it deploys
directly from `main.bicep`, no compiled ARM template needed.

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
- This IaC is validated at **compile time** (`bicep build`) and should be previewed with `az deployment group what-if`; the first live deploy is the real end-to-end test.
