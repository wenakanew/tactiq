targetScope = 'resourceGroup'

@description('Verified private database hostname.')
param databaseHost string = 'psql-tactiq-prod-3e4d.postgres.database.azure.com'
@allowed(['managed_identity', 'password'])
param postgresAuth string = 'managed_identity'
@description('Verified backend SHA256 image.')
param backendImage string
@secure()
@description('Bootstrap-only input; do not write to a parameters file or output.')
param bootstrapSecretJson string

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: 'cae-tactiq-prod-3e4d'
}
resource vault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: 'kv-tactiq-prod-3e4d'
}
var roles = ['api', 'jobs', 'bootstrap']
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = [for role in roles: {
  name: 'id-tactiq-${role}-3e4d'
}]
module bootstrap 'modules/bootstrap.bicep' = {
  name: 'tactiq-private-bootstrap'
  params: {
    location: 'westus3'
    tags: { application: 'tactiq', environment: 'prod-pilot', plan: 'tactiq-private-3e4d' }
    environmentId: environment.id
    identities: [for (role, i) in roles: {
      role: role
      id: identities[i].id
      name: identities[i].name
      clientId: identities[i].properties.clientId
      principalId: identities[i].properties.principalId
    }]
    registryHost: 'b8e2968c0n2026ca14acr.azurecr.io'
    backendImage: backendImage
    databaseHost: databaseHost
    postgresAuth: postgresAuth
    vaultUrl: vault.properties.vaultUri
    bootstrapSecretJson: bootstrapSecretJson
  }
}
