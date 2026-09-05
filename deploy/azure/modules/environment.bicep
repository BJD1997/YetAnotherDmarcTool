// Container Apps environment (VNet-injected) + Log Analytics + the one-off
// migrate Job. The migrate job creates the dmarc_app role, runs Alembic, and
// bootstraps the platform admin; it's started once by the deploymentScript in
// main.bicep, before the api/worker apps come up.

@description('Azure region.')
param location string
param namePrefix string
@description('Subnet delegated to Microsoft.App/environments.')
param acaSubnetId string
@description('User-assigned identity id (reads Key Vault).')
param identityId string
@description('Key Vault base URI, e.g. https://<name>.vault.azure.net/')
param vaultUri string
param appImage string
param platformAdminBootstrapEmail string = ''
@description('Whether a platform-admin bootstrap password secret was created in Key Vault.')
param deployBootstrapSecret bool = false
param logRetentionDays int = 30

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      // internal:false gives the environment a public LB so an external-ingress
      // app (the api) is reachable, while all apps still run inside the VNet.
      infrastructureSubnetId: acaSubnetId
      internal: false
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

var migrateSecrets = concat(
  [
    {
      name: 'migrate-database-url'
      keyVaultUrl: '${vaultUri}secrets/migrate-database-url'
      identity: identityId
    }
    {
      name: 'dmarc-app-db-password'
      keyVaultUrl: '${vaultUri}secrets/dmarc-app-db-password'
      identity: identityId
    }
    {
      name: 'fernet-key'
      keyVaultUrl: '${vaultUri}secrets/fernet-key'
      identity: identityId
    }
  ],
  deployBootstrapSecret ? [
    {
      name: 'platform-admin-bootstrap-password'
      keyVaultUrl: '${vaultUri}secrets/platform-admin-bootstrap-password'
      identity: identityId
    }
  ] : []
)

var migrateEnv = concat(
  [
    {
      name: 'DATABASE_URL'
      secretRef: 'migrate-database-url'
    }
    {
      name: 'DMARC_APP_DB_PASSWORD'
      secretRef: 'dmarc-app-db-password'
    }
    {
      name: 'FERNET_KEY'
      secretRef: 'fernet-key'
    }
    {
      name: 'UPDATE_CHECK_ENABLED'
      value: 'false'
    }
    {
      name: 'PLATFORM_ADMIN_BOOTSTRAP_EMAIL'
      value: platformAdminBootstrapEmail
    }
  ],
  deployBootstrapSecret ? [
    {
      name: 'PLATFORM_ADMIN_BOOTSTRAP_PASSWORD'
      secretRef: 'platform-admin-bootstrap-password'
    }
  ] : []
)

resource migrateJob 'Microsoft.App/jobs@2024-03-01' = {
  name: '${namePrefix}-migrate'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 1
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      secrets: migrateSecrets
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: appImage
          // role init (managed Postgres has no docker-entrypoint-initdb.d) -> schema -> admin bootstrap
          command: [
            'sh'
            '-c'
            'python -m app.scripts.ensure_app_role && alembic upgrade head && python -m app.scripts.bootstrap_platform_admin'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: migrateEnv
        }
      ]
    }
  }
}

output environmentId string = env.id
output defaultDomain string = env.properties.defaultDomain
output migrateJobName string = migrateJob.name
