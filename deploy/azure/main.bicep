// deploy/azure/main.bicep
// YetAnotherDmarcTool — Azure Container Apps deployment (one-click / Bicep).
//
// Stands up: a VNet (private Postgres + private Key Vault), Azure Database for
// PostgreSQL Flexible Server, Key Vault (+ user-assigned identity) behind a
// private endpoint, a Container Apps environment, the api + worker apps (each
// with a DNSSEC Unbound resolver sidecar), and a one-off migrate job that a
// deploymentScript runs before the apps start.
//
// deploymentSize (test/small/medium/large) drives every size-dependent value
// via the sizing lookup table below — the only place in this template tier
// logic is resolved; every module receives already-resolved plain values.
// postgresHighAvailability is deliberately independent of deploymentSize.
//
// Deploy: the "Deploy to Azure" button (see deploy/azure/README.md) or
//   az deployment group create -g <rg> -f deploy/azure/main.bicep -p @params.json
targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short prefix for resource names (letters/numbers).')
@minLength(2)
@maxLength(12)
param namePrefix string = 'yadt'

@description('Container image tag to deploy (the released version, or "latest").')
param imageTag string = 'latest'

@description('Deployment size — pre-fills network, Postgres, and replica-count sizing. Always individually overridable below.')
@allowed(['test', 'small', 'medium', 'large'])
param deploymentSize string = 'small'

@description('Postgres high availability — independent of deploymentSize. Roughly doubles Postgres compute cost when enabled.')
@allowed(['Disabled', 'ZoneRedundant'])
param postgresHighAvailability string = 'Disabled'

// --- required secrets ---
@description('Postgres administrator login.')
param administratorLogin string = 'dmarcadmin'
@secure()
@description('Postgres administrator password.')
param administratorPassword string
@secure()
@description('Password for the non-owner dmarc_app runtime role (created by the migrate job).')
param dmarcAppDbPassword string
@secure()
@description('Fernet key for encrypting TOTP secrets/credentials at rest. Generate: openssl rand -base64 32 | tr "+/" "-_"')
param fernetKey string

// --- platform admin bootstrap (optional but recommended for first login) ---
param platformAdminBootstrapEmail string = ''
@secure()
param platformAdminBootstrapPassword string = ''

// --- optional Entra (Azure AD) ---
param entraSsoClientId string = ''
@secure()
param entraSsoClientSecret string = ''
#disable-next-line no-hardcoded-env-urls
param entraSsoAuthority string = 'https://login.microsoftonline.com/organizations'
param entraMailClientId string = ''
@secure()
param entraMailClientSecret string = ''

// --- optional custom domain (bind the managed cert post-deploy; see README) ---
param publicBaseUrlOverride string = ''

// --- sizing overrides (each defaults to "use the tier's value"; -1 / empty means "unset, use tier default") ---
@description('Override the tier-derived Postgres SKU name. Empty = use deploymentSize default.')
param postgresSkuNameOverride string = ''
@description('Override the tier-derived Postgres SKU tier. Empty = use deploymentSize default.')
param postgresSkuTierOverride string = ''
@description('Override the tier-derived Postgres storage (GB). 0 = use deploymentSize default.')
param postgresStorageGBOverride int = 0
@description('Override the tier-derived api max replicas. 0 = use deploymentSize default.')
param apiMaxReplicasOverride int = 0
@description('Override the tier-derived worker max replicas. 0 = use deploymentSize default.')
param workerMaxReplicasOverride int = 0

// --- the sizing-tier lookup table: the ONLY place tier logic is resolved ---
var sizingTiers = {
  test: {
    vnetAddressPrefix: '10.20.0.0/25'
    acaSubnetPrefix: '10.20.0.32/27'
    pgSubnetPrefix: '10.20.0.8/29'
    keyVaultSubnetPrefix: '10.20.0.16/29'
    postgresSkuName: 'Standard_B1ms'
    postgresSkuTier: 'Burstable'
    postgresStorageGB: 32
    apiMinReplicas: 1
    apiMaxReplicas: 1
    workerMaxReplicas: 1
    logRetentionDays: 30
  }
  small: {
    vnetAddressPrefix: '10.20.0.0/24'
    acaSubnetPrefix: '10.20.0.0/26'
    pgSubnetPrefix: '10.20.0.128/28'
    keyVaultSubnetPrefix: '10.20.0.144/28'
    postgresSkuName: 'Standard_B2s'
    postgresSkuTier: 'Burstable'
    postgresStorageGB: 32
    apiMinReplicas: 1
    apiMaxReplicas: 2
    workerMaxReplicas: 2
    logRetentionDays: 30
  }
  medium: {
    vnetAddressPrefix: '10.20.0.0/23'
    acaSubnetPrefix: '10.20.0.0/25'
    pgSubnetPrefix: '10.20.1.128/28'
    keyVaultSubnetPrefix: '10.20.1.144/28'
    postgresSkuName: 'Standard_D2ds_v4'
    postgresSkuTier: 'GeneralPurpose'
    postgresStorageGB: 64
    apiMinReplicas: 1
    apiMaxReplicas: 3
    workerMaxReplicas: 3
    logRetentionDays: 60
  }
  large: {
    vnetAddressPrefix: '10.20.0.0/22'
    acaSubnetPrefix: '10.20.0.0/23'
    pgSubnetPrefix: '10.20.2.0/27'
    keyVaultSubnetPrefix: '10.20.2.32/28'
    postgresSkuName: 'Standard_D4ds_v4'
    postgresSkuTier: 'GeneralPurpose'
    postgresStorageGB: 128
    apiMinReplicas: 2
    apiMaxReplicas: 5
    workerMaxReplicas: 5
    logRetentionDays: 90
  }
}
var tier = sizingTiers[deploymentSize]

var resolvedPostgresSkuName = empty(postgresSkuNameOverride) ? tier.postgresSkuName : postgresSkuNameOverride
var resolvedPostgresSkuTier = empty(postgresSkuTierOverride) ? tier.postgresSkuTier : postgresSkuTierOverride
var resolvedPostgresStorageGB = postgresStorageGBOverride == 0 ? tier.postgresStorageGB : postgresStorageGBOverride
var resolvedApiMaxReplicas = apiMaxReplicasOverride == 0 ? tier.apiMaxReplicas : apiMaxReplicasOverride
var resolvedWorkerMaxReplicas = workerMaxReplicasOverride == 0 ? tier.workerMaxReplicas : workerMaxReplicasOverride

var suffix = uniqueString(resourceGroup().id)
var keyVaultName = take('${namePrefix}kv${suffix}', 24)
var pgServerName = toLower('${namePrefix}-pg-${suffix}')
var appImage = 'ghcr.io/bjd1997/yetanotherdmarctool:${imageTag}'
var resolverImage = 'ghcr.io/bjd1997/yetanotherdmarctool-resolver:${imageTag}'

var deployBootstrapSecret = !empty(platformAdminBootstrapPassword)
var deployEntraSsoSecret = !empty(entraSsoClientId) && !empty(entraSsoClientSecret)
var deployEntraMailSecret = !empty(entraMailClientId) && !empty(entraMailClientSecret)

var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    namePrefix: namePrefix
    vnetAddressPrefix: tier.vnetAddressPrefix
    acaSubnetPrefix: tier.acaSubnetPrefix
    pgSubnetPrefix: tier.pgSubnetPrefix
    keyVaultSubnetPrefix: tier.keyVaultSubnetPrefix
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    serverName: pgServerName
    administratorLogin: administratorLogin
    administratorPassword: administratorPassword
    delegatedSubnetId: network.outputs.pgSubnetId
    privateDnsZoneId: network.outputs.pgPrivateDnsZoneId
    skuName: resolvedPostgresSkuName
    skuTier: resolvedPostgresSkuTier
    storageSizeGB: resolvedPostgresStorageGB
    highAvailabilityMode: postgresHighAvailability
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    keyVaultName: keyVaultName
    identityName: '${namePrefix}-id'
    keyVaultSubnetId: network.outputs.keyVaultSubnetId
    kvPrivateDnsZoneId: network.outputs.kvPrivateDnsZoneId
    pgFqdn: postgres.outputs.serverFqdn
    databaseName: postgres.outputs.databaseName
    administratorLogin: administratorLogin
    administratorPassword: administratorPassword
    dmarcAppDbPassword: dmarcAppDbPassword
    fernetKey: fernetKey
    platformAdminBootstrapPassword: platformAdminBootstrapPassword
    entraMailClientSecret: entraMailClientSecret
    entraSsoClientSecret: entraSsoClientSecret
  }
}

module environment 'modules/environment.bicep' = {
  name: 'environment'
  params: {
    location: location
    namePrefix: namePrefix
    acaSubnetId: network.outputs.acaSubnetId
    identityId: keyvault.outputs.identityId
    vaultUri: keyvault.outputs.vaultUri
    appImage: appImage
    platformAdminBootstrapEmail: platformAdminBootstrapEmail
    deployBootstrapSecret: deployBootstrapSecret
    logRetentionDays: tier.logRetentionDays
  }
}

// Dedicated identity for the deploymentScript to start the migrate job.
resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-deploy-id'
  location: location
}

resource deployContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployIdentity.id, contributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Runs the migrate job (role init + Alembic + admin bootstrap) and waits for it,
// so the schema + dmarc_app role exist before the apps start. Only calls the ARM
// control plane, so it needs no VNet access.
resource runMigrate 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: '${namePrefix}-run-migrate'
  location: location
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${deployIdentity.id}': {}
    }
  }
  properties: {
    azCliVersion: '2.62.0'
    retentionInterval: 'PT1H'
    timeout: 'PT45M'
    cleanupPreference: 'OnSuccess'
    environmentVariables: [
      {
        name: 'RG'
        value: resourceGroup().name
      }
      {
        name: 'JOB'
        value: environment.outputs.migrateJobName
      }
    ]
    scriptContent: '''
      set -e
      echo "waiting for RBAC propagation before starting the migrate job..."
      sleep 45
      echo "starting migrate job $JOB in $RG"
      az containerapp job start -n "$JOB" -g "$RG" -o none
      for i in $(seq 1 80); do
        statuses=$(az containerapp job execution list -n "$JOB" -g "$RG" --query "[].properties.status" -o tsv 2>/dev/null || echo "")
        echo "poll $i: [$statuses]"
        if echo "$statuses" | grep -q "Succeeded"; then echo "migrate succeeded"; exit 0; fi
        if echo "$statuses" | grep -q "Failed"; then echo "migrate job failed"; exit 1; fi
        sleep 15
      done
      echo "timed out waiting for migrate job"; exit 1
    '''
  }
  dependsOn: [
    deployContributor
  ]
}

module apps 'modules/apps.bicep' = {
  name: 'apps'
  params: {
    location: location
    namePrefix: namePrefix
    environmentId: environment.outputs.environmentId
    envDefaultDomain: environment.outputs.defaultDomain
    identityId: keyvault.outputs.identityId
    vaultUri: keyvault.outputs.vaultUri
    appImage: appImage
    resolverImage: resolverImage
    imageTag: imageTag
    publicBaseUrlOverride: publicBaseUrlOverride
    apiMinReplicas: tier.apiMinReplicas
    apiMaxReplicas: resolvedApiMaxReplicas
    workerMinReplicas: 1
    workerMaxReplicas: resolvedWorkerMaxReplicas
    entraSsoClientId: entraSsoClientId
    entraSsoAuthority: entraSsoAuthority
    deployEntraSsoSecret: deployEntraSsoSecret
    entraMailClientId: entraMailClientId
    deployEntraMailSecret: deployEntraMailSecret
  }
  // Apps must not start until the schema + dmarc_app role exist.
  dependsOn: [
    runMigrate
  ]
}

output apiUrl string = apps.outputs.apiUrl
output apiFqdn string = apps.outputs.apiFqdn
output keyVaultName string = keyVaultName
output postgresServerName string = postgres.outputs.serverName
