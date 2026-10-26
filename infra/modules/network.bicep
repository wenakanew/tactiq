@description('Approved region.')
param location string
@description('Resource tags.')
param tags object

resource databaseNsg 'Microsoft.Network/networkSecurityGroups@2024-07-01' = {
  name: 'nsg-postgres-3e4d'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowAppsPostgres'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '10.42.0.0/23'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.42.2.0/27'
          destinationPortRange: '5432'
        }
      }
      {
        name: 'AllowPostgresInternal'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '10.42.2.0/27'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.42.2.0/27'
          destinationPortRange: '5432'
        }
      }
      {
        name: 'DenyOtherPostgres'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Deny'
          protocol: 'Tcp'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.42.2.0/27'
          destinationPortRange: '5432'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-07-01' = {
  name: 'vnet-tactiq-prod-3e4d'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.42.0.0/16'] }
    subnets: [
      {
        name: 'snet-apps'
        properties: {
          addressPrefix: '10.42.0.0/23'
          delegations: [
            { name: 'apps', properties: { serviceName: 'Microsoft.App/environments' } }
          ]
        }
      }
      {
        name: 'snet-postgres'
        properties: {
          addressPrefix: '10.42.2.0/27'
          networkSecurityGroup: { id: databaseNsg.id }
          delegations: [
            { name: 'postgres', properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' } }
          ]
          serviceEndpoints: [
            { service: 'Microsoft.Storage', locations: [location] }
          ]
        }
      }
      {
        name: 'snet-endpoints'
        properties: {
          addressPrefix: '10.42.3.0/27'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

var zoneNames = [
  'tactiq-3e4d.postgres.database.azure.com'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.vaultcore.azure.net'
]
resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for name in zoneNames: {
  name: name
  location: 'global'
  tags: tags
}]
resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (name, i) in zoneNames: {
  parent: zones[i]
  name: 'tactiq'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}]

output appsSubnetId string = '${vnet.id}/subnets/snet-apps'
output databaseSubnetId string = '${vnet.id}/subnets/snet-postgres'
output endpointSubnetId string = '${vnet.id}/subnets/snet-endpoints'
output databaseDnsId string = zones[0].id
output blobDnsId string = zones[1].id
output vaultDnsId string = zones[2].id
