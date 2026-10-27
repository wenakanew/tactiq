targetScope = 'resourceGroup'

param backendImage string
param webImage string
param microsoftClientId string = '886015c9-8273-4b63-b631-531e54f5f865'
param foundryProjectEndpoint string
param foundryDeployment string
param budgetPeriodStart string
param budgetPeriodEnd string
param trustedProxyCidrs string
param activateJobs bool = false
param warmServing bool = false
param cvModelPath string = ''
param cvModelSha256 string = ''
param speechEnabled bool = false
param speechRegion string = 'southafricanorth'
param speechVoicesJson string = '{}'
param speechPairsJson string = '{}'

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: 'cae-tactiq-prod-3e4d'
}
var roles = ['api', 'jobs', 'web']
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = [for role in roles: {
  name: 'id-tactiq-${role}-3e4d'
}]
resource vault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: 'kv-tactiq-prod-3e4d'
}
var apiNames = concat(['ip-hmac-key', 'microsoft-client-certificate', 'foundry-api-key', 'postgres-api-dsn', 'postgres-ca-pem'], speechEnabled ? ['speech-api-key'] : [])
var jobsNames = ['ip-hmac-key', 'postgres-jobs-dsn', 'postgres-ca-pem']
resource apiSecrets 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = [for name in apiNames: {
  parent: vault
  name: name
}]
resource jobsSecrets 'Microsoft.KeyVault/vaults/secrets@2024-11-01' existing = [for name in jobsNames: {
  parent: vault
  name: name
}]
resource apiReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in apiNames: {
  name: guid(vault.id, name, identities[0].id, 'secret-read')
  scope: apiSecrets[i]
  properties: {
    principalId: identities[0].properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}]
resource jobsReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in jobsNames: {
  name: guid(vault.id, name, identities[1].id, 'secret-read')
  scope: jobsSecrets[i]
  properties: {
    principalId: identities[1].properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}]
module runtime 'modules/runtime.bicep' = {
  name: 'tactiq-runtime-connected'
  params: {
    location: 'westus3'
    tags: { application: 'tactiq', environment: 'prod-pilot', plan: 'tactiq-private-3e4d' }
    environmentId: environment.id
    defaultDomain: environment.properties.defaultDomain
    identities: [for (role, i) in roles: {
      role: role
      id: identities[i].id
      name: identities[i].name
      clientId: identities[i].properties.clientId
      principalId: identities[i].properties.principalId
    }]
    registryHost: 'b8e2968c0n2026ca14acr.azurecr.io'
    backendImage: backendImage
    webImage: webImage
    databaseHost: 'db.tactiq.internal'
    databaseAuth: 'password'
    blobUrl: 'https://sttactiqprod3e4d.blob.${az.environment().suffixes.storage}/'
    vaultUrl: vault.properties.vaultUri
    namespaceHost: 'sbns-tactiq-prod-3e4d.servicebus.windows.net'
    queueName: 'analysis'
    microsoftClientId: microsoftClientId
    microsoftAuth: 'certificate'
    foundryProjectEndpoint: foundryProjectEndpoint
    foundryDeployment: foundryDeployment
    budgetPeriodStart: budgetPeriodStart
    budgetPeriodEnd: budgetPeriodEnd
    trustedProxyCidrs: trustedProxyCidrs
    activateJobs: activateJobs
    warmServing: warmServing
    cvModelPath: cvModelPath
    cvModelSha256: cvModelSha256
    speechEnabled: speechEnabled
    speechRegion: speechRegion
    speechVoicesJson: speechVoicesJson
    speechPairsJson: speechPairsJson
  }
  dependsOn: [apiReaders, jobsReaders]
}
output publicOrigin string = runtime.outputs.publicOrigin
