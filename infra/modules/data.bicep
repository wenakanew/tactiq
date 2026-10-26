@description('Approved region.')
param location string
@description('Resource tags.')
param tags object
@description('Private delegated PostgreSQL subnet.')
param databaseSubnetId string
@description('Nondelegated private-endpoint subnet.')
param endpointSubnetId string
@description('PostgreSQL private DNS zone linked to the VNet.')
param databaseDnsId string
@description('Blob private DNS zone.')
param blobDnsId string
@description('Key Vault private DNS zone.')
param vaultDnsId string
@description('Tactiq API, worker, web, bootstrap and build identities.')
param identities array
@description('Grant per-secret runtime reads only after private bootstrap populated the vault.')
param runtimeSecretsReady bool
@description('Include the optional verified Speech secret only when Speech is enabled.')
param speechSecretsReady bool
@description('Bootstrap admin/writer creation is not reissued during runtime deployment; remove prior grants after bootstrap verification.')
param bootstrapAccessEnabled bool

var api = first(filter(identities, i => i.role == 'api'))
var jobs = first(filter(identities, i => i.role == 'jobs'))
var bootstrap = first(filter(identities, i => i.role == 'bootstrap'))

resource database 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: 'psql-tactiq-prod-3e4d'
  location: location
  tags: tags
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '17'
    availabilityZone: '1'
    createMode: 'Create'
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled'
      tenantId: tenant().tenantId
    }
    network: {
      delegatedSubnetResourceId: databaseSubnetId
      privateDnsZoneArmResourceId: databaseDnsId
      publicNetworkAccess: 'Disabled'
    }
    storage: { storageSizeGB: 32, autoGrow: 'Disabled', type: 'Premium_LRS' }
    highAvailability: { mode: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    dataEncryption: { type: 'SystemManaged' }
  }
}
resource administrator 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = if (bootstrapAccessEnabled) {
  parent: database
  name: bootstrap.principalId
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

resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' = {
  // checkov:skip=CKV_AZURE_36:Private-endpoint-only storage intentionally denies trusted-service public bypass.
  // checkov:skip=CKV_AZURE_206:Owner-approved single-region LRS pilot; replication upgrade is outside the cost plan.
  name: 'sttactiqprod3e4d'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: 'None' }
    encryption: {
      keySource: 'Microsoft.Storage'
      requireInfrastructureEncryption: true
      services: { blob: { enabled: true, keyType: 'Account' } }
    }
  }
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2025-01-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
    isVersioningEnabled: false
  }
}
resource media 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobService
  name: 'media'
  properties: { publicAccess: 'None' }
}
resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: 'kv-tactiq-prod-3e4d'
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Disabled'
    networkAcls: { defaultAction: 'Deny', bypass: 'None' }
  }
}
resource blobEndpoint 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pep-blob-tactiq-3e4d'
  location: location
  tags: tags
  properties: {
    subnet: { id: endpointSubnetId }
    privateLinkServiceConnections: [
      { name: 'blob', properties: { privateLinkServiceId: storage.id, groupIds: ['blob'] } }
    ]
  }
}
resource blobZone 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: blobEndpoint
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'blob', properties: { privateDnsZoneId: blobDnsId } }] }
}
resource vaultEndpoint 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pep-vault-tactiq-3e4d'
  location: location
  tags: tags
  properties: {
    subnet: { id: endpointSubnetId }
    privateLinkServiceConnections: [
      { name: 'vault', properties: { privateLinkServiceId: vault.id, groupIds: ['vault'] } }
    ]
  }
}
resource vaultZone 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: vaultEndpoint
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'vault', properties: { privateDnsZoneId: vaultDnsId } }] }
}
resource blobGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for identity in [api, jobs]: {
  name: guid(media.id, identity.principalId, 'blob-data')
  scope: media
  properties: {
    principalId: identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}]
var secretNames = concat(['ip-hmac-key', 'microsoft-client-secret', 'foundry-api-key'], speechSecretsReady ? ['speech-api-key'] : [])
resource secretScopes 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = [for name in secretNames: {
  parent: vault
  name: name
}]
resource apiSecretReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in secretNames: if (runtimeSecretsReady) {
  name: guid(vault.id, name, api.principalId, 'secret-read')
  scope: secretScopes[i]
  properties: {
    principalId: api.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}]
resource jobsSecretReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (runtimeSecretsReady) {
  name: guid(vault.id, 'ip-hmac-key', jobs.principalId, 'secret-read')
  scope: secretScopes[0]
  properties: {
    principalId: jobs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}
resource secretsBootstrap 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (bootstrapAccessEnabled) {
  name: guid(vault.id, bootstrap.principalId, 'bootstrap-secrets')
  scope: vault
  properties: {
    principalId: bootstrap.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  }
}

output databaseHost string = database.properties.fullyQualifiedDomainName
output blobUrl string = storage.properties.primaryEndpoints.blob
output vaultUrl string = vault.properties.vaultUri
output vaultId string = vault.id
