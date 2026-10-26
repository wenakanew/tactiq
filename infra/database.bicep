targetScope = 'resourceGroup'

@description('Same-price allocation attempt in a different advertised westus3 zone.')
@allowed(['1', '2', '3'])
param availabilityZone string = '2'

@description('Existing bootstrap managed identity object ID, verified before deployment.')
@minLength(36)
param bootstrapPrincipalId string

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-07-01' existing = {
  name: 'vnet-tactiq-prod-3e4d/snet-postgres'
}
resource dns 'Microsoft.Network/privateDnsZones@2024-06-01' existing = {
  name: 'tactiq-3e4d.postgres.database.azure.com'
}
resource bootstrap 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: 'id-tactiq-bootstrap-3e4d'
}
resource database 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: 'psql-tactiq-prod-3e4d'
  location: 'westus3'
  tags: { application: 'tactiq', environment: 'prod-pilot', plan: 'tactiq-private-3e4d' }
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '17'
    availabilityZone: availabilityZone
    createMode: 'Create'
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenant().tenantId
    }
    network: {
      delegatedSubnetResourceId: subnet.id
      privateDnsZoneArmResourceId: dns.id
      publicNetworkAccess: 'Disabled'
    }
    storage: { storageSizeGB: 32, autoGrow: 'Disabled', type: 'Premium_LRS' }
    highAvailability: { mode: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    dataEncryption: { type: 'SystemManaged' }
  }
}
resource administrator 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = {
  parent: database
  name: bootstrapPrincipalId
  properties: {
    principalName: bootstrap.name
    principalType: 'ServicePrincipal'
    tenantId: tenant().tenantId
  }
}
resource applicationDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: database
  name: 'tactiq'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}
resource tls 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: database
  name: 'ssl_min_protocol_version'
  properties: { value: 'TLSv1.2', source: 'user-override' }
}
output databaseHost string = database.properties.fullyQualifiedDomainName
