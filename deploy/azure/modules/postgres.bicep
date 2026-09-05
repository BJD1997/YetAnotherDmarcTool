// Azure Database for PostgreSQL Flexible Server, private (VNet-integrated) — no
// public endpoint. The role/schema init (creating the non-owner dmarc_app role
// and running Alembic) is done by the migrate ACA Job, not here.
//
// Ordering: this module takes the private DNS zone id (a network-module output)
// as a param, which makes Bicep run the whole network module first — including
// the zone<->VNet link the server requires — so no explicit dependsOn is needed.

@description('Azure region.')
param location string
param serverName string
param databaseName string = 'dmarc'

param administratorLogin string
@secure()
param administratorPassword string

@description('Subnet delegated to Microsoft.DBforPostgreSQL/flexibleServers.')
param delegatedSubnetId string
@description('Private DNS zone (privatelink.postgres.database.azure.com) resource id.')
param privateDnsZoneId string

param version string = '16'
param skuName string = 'Standard_B1ms'
param skuTier string = 'Burstable'
param storageSizeGB int = 32
param backupRetentionDays int = 7

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: serverName
  location: location
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    version: version
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: {
      storageSizeGB: storageSizeGB
    }
    network: {
      delegatedSubnetResourceId: delegatedSubnetId
      privateDnsZoneArmResourceId: privateDnsZoneId
    }
    highAvailability: {
      mode: 'Disabled'
    }
    backup: {
      backupRetentionDays: backupRetentionDays
      geoRedundantBackup: 'Disabled'
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

output serverFqdn string = pg.properties.fullyQualifiedDomainName
output serverName string = pg.name
output databaseName string = databaseName
