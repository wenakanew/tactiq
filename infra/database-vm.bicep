targetScope = 'resourceGroup'

@description('One-use generated SSH public key; SSH ingress is denied and no private key is retained.')
@secure()
param adminPublicKey string

param adminUsername string = 'tactiqadmin'
@description('Temporarily permit Run Command initialization; disable after verification.')
param initializationEnabled bool = false

var location = 'westus3'
var tags = { application: 'tactiq', environment: 'prod-pilot', plan: 'tactiq-private-3e4d' }
resource vnet 'Microsoft.Network/virtualNetworks@2024-07-01' existing = {
  name: 'vnet-tactiq-prod-3e4d'
}
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-07-01' = {
  name: 'nsg-tactiq-db-vm-3e4d'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowPrivateApplicationPostgres'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: '10.42.0.0/23'
          sourcePortRange: '*'
          destinationAddressPrefix: '10.42.4.4'
          destinationPortRange: '5432'
        }
      }
      {
        name: 'DenyAllOtherInbound'
        properties: {
          priority: 200
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}
resource subnet 'Microsoft.Network/virtualNetworks/subnets@2024-07-01' = {
  parent: vnet
  name: 'snet-database-vm'
  properties: {
    addressPrefix: '10.42.4.0/27'
    networkSecurityGroup: { id: nsg.id }
    defaultOutboundAccess: false
    privateEndpointNetworkPolicies: 'Disabled'
  }
}
resource outbound 'Microsoft.Network/publicIPAddresses@2024-07-01' = {
  name: 'pip-tactiq-db-outbound-3e4d'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Regional' }
  properties: { publicIPAllocationMethod: 'Static', publicIPAddressVersion: 'IPv4' }
}
resource nic 'Microsoft.Network/networkInterfaces@2024-07-01' = {
  name: 'nic-tactiq-db-3e4d'
  location: location
  tags: tags
  properties: {
    enableIPForwarding: false
    networkSecurityGroup: { id: nsg.id }
    ipConfigurations: [{
      name: 'private'
      properties: {
        primary: true
        privateIPAllocationMethod: 'Static'
        privateIPAddress: '10.42.4.4'
        subnet: { id: subnet.id }
        publicIPAddress: { id: outbound.id }
      }
    }]
  }
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-tactiq-db-3e4d'
  location: location
  tags: tags
}
resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  // checkov:skip=CKV_AZURE_50:Temporary VM-agent Run Command initialization is required because SSH ingress is denied; disable extension operations after verification.
  name: 'vm-tactiq-db-3e4d'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    hardwareProfile: { vmSize: 'Standard_B2ats_v2' }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
      encryptionAtHost: true
    }
    storageProfile: {
      imageReference: { publisher: 'Debian', offer: 'debian-13', sku: '13-gen2', version: 'latest' }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 32
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Detach'
      }
      dataDisks: [{
        lun: 0
        name: 'disk-tactiq-postgres-3e4d'
        createOption: 'Empty'
        diskSizeGB: 32
        managedDisk: { storageAccountType: 'StandardSSD_LRS' }
        deleteOption: 'Detach'
        caching: 'None'
      }]
    }
    osProfile: {
      computerName: 'tactiq-db'
      adminUsername: adminUsername
      allowExtensionOperations: initializationEnabled
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: { publicKeys: [{ path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: adminPublicKey }] }
        patchSettings: { patchMode: 'ImageDefault', assessmentMode: 'ImageDefault' }
      }
    }
    networkProfile: { networkInterfaces: [{ id: nic.id, properties: { primary: true } }] }
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}
resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'tactiq.internal'
  location: 'global'
  tags: tags
}
resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: 'tactiq'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: vnet.id } }
}
resource record 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: 'db'
  properties: { ttl: 60, aRecords: [{ ipv4Address: '10.42.4.4' }] }
}
resource vault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: 'kv-tactiq-prod-3e4d'
}
resource initializeSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (initializationEnabled) {
  name: guid(vault.id, identity.id, 'initialize-db-secrets')
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  }
}
resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' existing = {
  name: 'sttactiqprod3e4d'
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2025-01-01' existing = {
  parent: storage
  name: 'default'
}
resource backups 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobs
  name: 'database-backups'
  properties: { publicAccess: 'None' }
}
resource backupWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backups.id, identity.id, 'database-backups')
  scope: backups
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource retention 'Microsoft.Storage/storageAccounts/managementPolicies@2025-01-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: { rules: [{
      enabled: true
      name: 'expire-database-backups'
      type: 'Lifecycle'
      definition: {
        filters: { blobTypes: ['blockBlob'], prefixMatch: ['database-backups/'] }
        actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 8 } } }
      }
    }] }
  }
}
output databaseHost string = 'db.tactiq.internal'
output databaseIdentityClientId string = identity.properties.clientId
output initializationGrantId string = initializationEnabled ? initializeSecrets.id : ''
