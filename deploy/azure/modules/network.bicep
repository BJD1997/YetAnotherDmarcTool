// deploy/azure/modules/network.bicep
// VNet with three subnets — one delegated to the ACA environment, one
// delegated to the Postgres Flexible Server, and one (non-delegated) for the
// Key Vault private endpoint — plus the two private DNS zones (Postgres,
// Key Vault) so the apps reach both privately. CIDR sizing is tier-driven by
// the caller (main.bicep) via vnetAddressPrefix/acaSubnetPrefix/etc — this
// module has no tier-awareness of its own. See ../main.bicep.

@description('Azure region for all resources.')
param location string

@description('Base name used to derive resource names.')
param namePrefix string

@description('VNet address space, sized by the caller per deployment tier.')
param vnetAddressPrefix string
@description('Subnet delegated to the Container Apps environment.')
param acaSubnetPrefix string
@description('Subnet delegated to the Postgres Flexible Server.')
param pgSubnetPrefix string
@description('Non-delegated subnet for the Key Vault private endpoint.')
param keyVaultSubnetPrefix string

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${namePrefix}-vnet'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: 'aca'
        properties: {
          addressPrefix: acaSubnetPrefix
          delegations: [
            {
              name: 'aca-delegation'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'postgres'
        properties: {
          addressPrefix: pgSubnetPrefix
          delegations: [
            {
              name: 'pg-delegation'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
      {
        name: 'keyvault-pe'
        properties: {
          addressPrefix: keyVaultSubnetPrefix
          // Private endpoints and service delegation are mutually exclusive
          // on one subnet — deliberately no delegations here.
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource pgPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.postgres.database.azure.com'
  location: 'global'
}

resource pgDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: pgPrivateDnsZone
  name: '${namePrefix}-pg-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource kvPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
}

resource kvDnsVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: kvPrivateDnsZone
  name: '${namePrefix}-kv-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

output acaSubnetId string = vnet.properties.subnets[0].id
output pgSubnetId string = vnet.properties.subnets[1].id
output keyVaultSubnetId string = vnet.properties.subnets[2].id
// The Flexible Server and the KV private endpoint each take this module's
// *PrivateDnsZoneId output and reference it directly, which already gives
// Bicep an implicit dependency on the zone (and, transitively, its VNet
// link) completing first — so main.bicep needs no explicit dependsOn here.
// The link-id outputs are exposed for completeness/debugging (e.g.
// confirming the link exists via `az network private-dns link vnet show`)
// but aren't consumed by any other module today.
output pgPrivateDnsZoneId string = pgPrivateDnsZone.id
output pgDnsVnetLinkId string = pgDnsVnetLink.id
output kvPrivateDnsZoneId string = kvPrivateDnsZone.id
output kvDnsVnetLinkId string = kvDnsVnetLink.id
