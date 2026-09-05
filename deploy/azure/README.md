# Deploy on Azure Container Apps

One-click, autoscaling deployment of YetAnotherDmarcTool on **Azure Container Apps**,
with a **private (VNet-integrated) PostgreSQL Flexible Server** and **Azure Key Vault**.
Bicep is the source of truth (`main.bicep` + `modules/`); `azuredeploy.json` is the
compiled ARM the Portal button uses.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.4-beta%2Fdeploy%2Fazure%2Fazuredeploy.json/createUIDefinitionUri/https%3A%2F%2Fraw.githubusercontent.com%2FBJD1997%2FYetAnotherDmarcTool%2Fv0.1.4-beta%2Fdeploy%2Fazure%2FcreateUiDefinition.json)

## What it creates
- **VNet** with two delegated subnets (Container Apps, Postgres) + a `privatelink.postgres.database.azure.com` private DNS zone.
- **Azure Database for PostgreSQL Flexible Server** — private access only (no public endpoint).
- **Key Vault** + a user-assigned managed identity (the apps read secrets via Key Vault references).
- **Container Apps environment** (VNet-injected) + **Log Analytics**.
- **api** app — external HTTPS ingress, autoscales on HTTP concurrency; **worker** app — internal, autoscales on the background-job queue depth (KEDA Postgres scaler, min 1 so a leader always exists). Each runs a **DNSSEC Unbound resolver sidecar** (ACA has no UDP ingress, so DNS is over `127.0.0.1`).
- **migrate job** — creates the non-owner `dmarc_app` role, runs Alembic, bootstraps the platform admin. A `deploymentScript` runs it during deployment, before the apps start.

## Prerequisites
- An Azure subscription, and **Owner** (or **Contributor + User Access Administrator**) on the target resource group — the template creates **role assignments** (Key Vault access for the app identity; Contributor for the migrate deployment script).
- These resource providers registered (Portal usually auto-registers; via CLI: `az provider register -n <NS>`): `Microsoft.App`, `Microsoft.DBforPostgreSQL`, `Microsoft.KeyVault`, `Microsoft.OperationalInsights`, `Microsoft.ManagedIdentity`, `Microsoft.ContainerInstance` (used by the deployment script), `Microsoft.Network`.
- A **Fernet key** for `fernetKey`: `openssl rand -base64 32 | tr '+/' '-_'`.

## Parameters (the ones you'll set)
| Parameter | Required | Notes |
|---|---|---|
| `namePrefix` | yes | 2–12 chars, lowercase-letter first. Prefix for resource names. |
| `imageTag` | yes | Release tag (e.g. `v0.1.4`) or `latest`. |
| `administratorLogin` / `administratorPassword` | yes | Postgres admin (used only by the migrate job). |
| `dmarcAppDbPassword` | yes | Password for the non-owner `dmarc_app` role the app connects as. |
| `fernetKey` | yes | Encrypts TOTP secrets/credentials at rest. |
| `platformAdminBootstrapEmail` / `…Password` | recommended | First platform-admin login, created if none exists. |
| `entra*` | optional | Entra SSO (api) and/or Graph mailbox ingestion (worker). Leave blank for local auth + DNS-checks-only. |
| `publicBaseUrlOverride` | optional | A custom domain URL; leave blank to use the default ACA URL. |
| `apiMaxReplicas` / `workerMaxReplicas` | optional | Autoscale ceilings. |

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
Re-run the deployment (button or CLI) with a new `imageTag`, or: `az containerapp update -n <namePrefix>-api -g <rg> --image ghcr.io/bjd1997/yetanotherdmarctool:<tag>` (and the same for `-worker`). If a release adds migrations, run the migrate job again: `az containerapp job start -n <namePrefix>-migrate -g <rg>`.

## Manual deploy (instead of the button)
```bash
az group create -n <rg> -l <region>
cp deploy/azure/main.parameters.example.json my.parameters.json   # fill in secrets
az deployment group what-if -g <rg> -f deploy/azure/main.bicep -p @my.parameters.json   # preview
az deployment group create   -g <rg> -f deploy/azure/main.bicep -p @my.parameters.json
```
If the in-deployment migrate step ever doesn't run, trigger it yourself:
`az containerapp job start -n <namePrefix>-migrate -g <rg>`.

## Notes & trade-offs
- **Key Vault** is reachable over its public endpoint, gated by the identity's RBAC (no data-plane access policies). Lock it to a private endpoint if your policy requires it.
- **Cost**: Container Apps Consumption (scale-to-1 here for the always-on leader), a Burstable `Standard_B1ms` Postgres, Key Vault, and Log Analytics — a small always-on baseline. Raise the Postgres SKU / replica ceilings for real load.
- This IaC is validated at **compile time** (`bicep build`) and should be previewed with `az deployment group what-if`; the first live deploy is the real end-to-end test.
