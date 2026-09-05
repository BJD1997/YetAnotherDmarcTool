// VNet with two delegated subnets (one for the ACA environment, one for the
// Postgres Flexible Server) and a private DNS zone so the apps reach Postgres
// privately. See ../main.bicep.

@description('Azure region for all resources.')
param location string

@description('Base name used to derive resource names.')
param namePrefix string

param vnetAddressPrefix string = '10.20.0.0/16'
@description('Subnet delegated to the Container Apps environment (min /27 for workload profiles).')
param acaSubnetPrefix string = '10.20.0.0/23'
@description('Subnet delegated to the Postgres Flexible Server.')
param pgSubnetPrefix string = '10.20.2.0/28'

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

output acaSubnetId string = vnet.properties.subnets[0].id
output pgSubnetId string = vnet.properties.subnets[1].id
// The Flexible Server needs the zone linked to the VNet before it's created;
// expose the link's id so the server can dependsOn it.
output pgPrivateDnsZoneId string = pgPrivateDnsZone.id
output pgDnsVnetLinkId string = pgDnsVnetLink.id
