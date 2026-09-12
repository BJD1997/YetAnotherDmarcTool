// deploy/azure/modules/keyvault.bicep
// Key Vault holding every secret, a user-assigned managed identity the
// container apps use to read them (Key Vault references), and the role
// assignment granting that identity read access. Reached over a private
// endpoint (supersedes an earlier public-endpoint-plus-RBAC design) — the
// apps resolve Key Vault references over the VNet, via the private DNS zone
// the network module links to the same VNet. Connection strings are composed
// here from the Postgres FQDN + secure params so the apps only ever see a
// Key Vault reference.

@description('Azure region.')
param location string
param keyVaultName string
param identityName string
param tenantId string = subscription().tenantId

@description('Non-delegated subnet for the private endpoint.')
param keyVaultSubnetId string
@description('privatelink.vaultcore.azure.net private DNS zone resource id.')
param kvPrivateDnsZoneId string

// Postgres coordinates (from the postgres module) used to compose connection strings.
param pgFqdn string
param databaseName string
param administratorLogin string

@secure()
param administratorPassword string
@secure()
param dmarcAppDbPassword string
@secure()
param fernetKey string
@secure()
param platformAdminBootstrapPassword string = ''
@secure()
param entraMailClientSecret string = ''
@secure()
param entraSsoClientSecret string = ''

var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Private endpoint only — the ACA environment is VNet-integrated and the
    // private DNS zone is linked to the same VNet, so Key Vault reference
    // resolution stays transparent to the apps. No public network path.
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      // AzureServices, not None: Key Vault firewall rules (this networkAcls
      // block) only ever apply to data-plane operations — Microsoft's docs
      // confirm ARM control-plane calls, including writing the
      // Microsoft.KeyVault/vaults/secrets resources below via this same
      // template, are never subject to them regardless of this setting. So
      // AzureServices isn't required for the deployment to succeed; it's a
      // deliberate, narrow safety margin for other Azure trusted services
      // that do go over the data plane, kept intentionally tight — arbitrary
      // internet/public traffic is still denied by publicNetworkAccess:
      // 'Disabled' and defaultAction: 'Deny' above.
      bypass: 'AzureServices'
    }
  }
}

resource kvPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${keyVaultName}-pe'
  location: location
  properties: {
    subnet: {
      id: keyVaultSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${keyVaultName}-pls'
        properties: {
          privateLinkServiceId: kv.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource kvDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: kvPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vaultcore'
        properties: {
          privateDnsZoneId: kvPrivateDnsZoneId
        }
      }
    ]
  }
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, identity.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// The runtime (dmarc_app) connection used by api/worker.
resource sAppDbUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'app-database-url'
  properties: {
    value: 'postgresql+asyncpg://dmarc_app:${dmarcAppDbPassword}@${pgFqdn}:5432/${databaseName}'
  }
}

// The admin connection used only by the migrate job (DDL, CREATE ROLE, GRANT).
resource sMigrateDbUrl 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'migrate-database-url'
  properties: {
    value: 'postgresql+asyncpg://${administratorLogin}:${administratorPassword}@${pgFqdn}:5432/${databaseName}'
  }
}

resource sDmarcAppPw 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'dmarc-app-db-password'
  properties: {
    value: dmarcAppDbPassword
  }
}

resource sFernet 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'fernet-key'
  properties: {
    value: fernetKey
  }
}

// libpq DSN for the worker's KEDA postgresql scale rule.
resource sKedaConn 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'keda-pg-connection'
  properties: {
    value: 'host=${pgFqdn} port=5432 dbname=${databaseName} user=dmarc_app password=${dmarcAppDbPassword} sslmode=require'
  }
}

resource sBootstrapPw 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(platformAdminBootstrapPassword)) {
  parent: kv
  name: 'platform-admin-bootstrap-password'
  properties: {
    value: platformAdminBootstrapPassword
  }
}

resource sEntraMail 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(entraMailClientSecret)) {
  parent: kv
  name: 'entra-mail-client-secret'
  properties: {
    value: entraMailClientSecret
  }
}

resource sEntraSso 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!empty(entraSsoClientSecret)) {
  parent: kv
  name: 'entra-sso-client-secret'
  properties: {
    value: entraSsoClientSecret
  }
}

output identityId string = identity.id
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
output vaultUri string = kv.properties.vaultUri
output roleAssignmentId string = kvSecretsUser.id
