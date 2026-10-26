targetScope = 'subscription'

@description('Approved resource group; referenced shared resources are outside it and never recreated.')
@allowed(['rg-tactiq-prod-3e4d'])
param resourceGroupName string = 'rg-tactiq-prod-3e4d'
@description('Approved region.')
@allowed(['westus3'])
param location string = 'westus3'
@description('Incremental release stage. No stage starts a manual job.')
@allowed(['foundation', 'bootstrap', 'runtime'])
param stage string = 'foundation'
@description('Existing registry resource group.')
@allowed(['My-projects'])
param registryResourceGroup string = 'My-projects'
@description('Existing ABAC-enabled Standard registry.')
@allowed(['b8e2968c0n2026ca14acr'])
param registryName string = 'b8e2968c0n2026ca14acr'
@description('Backend sha256-pinned image; required for bootstrap/runtime. No mutable tag is supplied.')
param backendImage string = ''
@description('Frontend sha256-pinned image built with the generated internal API origin; required for runtime.')
param webImage string = ''
@description('Existing Foundry project endpoint; supplied explicitly for runtime.')
param foundryProjectEndpoint string = ''
@description('Existing Foundry model deployment name.')
param foundryDeployment string = ''
@description('Microsoft application ID with exact public callback registered separately.')
param microsoftClientId string = ''
@description('Explicit approved fixed budget start epoch seconds for runtime.')
param budgetPeriodStart string = ''
@description('Explicit approved fixed budget end epoch seconds for runtime.')
param budgetPeriodEnd string = ''
@description('Existing Speech region; not a new account.')
param speechRegion string = 'southafricanorth'
@description('Only enable after voice prices and live Speech results are verified.')
param speechEnabled bool = false
@description('Verified conservative Speech price per million characters in microUSD.')
param speechPrice int = 30000000
@description('Verified single voice mapping JSON.')
param speechVoicesJson string = '{}'
@description('Verified dual voice mapping JSON.')
param speechPairsJson string = '{}'
@description('Leave false until runtime release checks pass; true enables approved schedule and queue scaling.')
param activateJobs bool = false
@description('Licensed ONNX artifact packaged in worker image, not downloaded at request time.')
param cvModelPath string = ''
@description('Verified packaged model sha256.')
param cvModelSha256 string = ''
@description('Verified trusted ingress proxy CIDRs for public client-IP accounting; required for runtime.')
param trustedProxyCidrs string = ''
@description('Bootstrap-only secret map, delivered securely from process environment. Never output or commit values.')
@secure()
param bootstrapSecretJson string = ''

var tags = { application: 'tactiq', environment: 'prod-pilot', plan: 'tactiq-private-3e4d' }
resource group 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}
module network 'modules/network.bicep' = {
  name: 'tactiq-network'
  scope: group
  params: { location: location, tags: tags }
}
module identities 'modules/identities.bicep' = {
  name: 'tactiq-identities'
  scope: group
  params: { location: location, buildLocation: 'southafricanorth', tags: tags }
}
module registry 'modules/registry-access.bicep' = {
  name: 'tactiq-registry-access'
  scope: resourceGroup(registryResourceGroup)
  params: { registryName: registryName, identities: identities.outputs.identities }
}
module data 'modules/data.bicep' = {
  name: 'tactiq-data'
  scope: group
  params: {
    location: location
    tags: tags
    databaseSubnetId: network.outputs.databaseSubnetId
    endpointSubnetId: network.outputs.endpointSubnetId
    databaseDnsId: network.outputs.databaseDnsId
    blobDnsId: network.outputs.blobDnsId
    vaultDnsId: network.outputs.vaultDnsId
    identities: identities.outputs.identities
    runtimeSecretsReady: stage == 'runtime'
    speechSecretsReady: speechEnabled && stage == 'runtime'
    bootstrapAccessEnabled: stage != 'runtime'
  }
}
module platform 'modules/platform.bicep' = {
  name: 'tactiq-platform'
  scope: group
  params: {
    location: location
    tags: tags
    appsSubnetId: network.outputs.appsSubnetId
    jobsIdentity: first(filter(identities.outputs.identities, i => i.role == 'jobs'))
  }
}
module bootstrap 'modules/bootstrap.bicep' = if (stage == 'bootstrap') {
  name: 'tactiq-bootstrap'
  scope: group
  params: {
    location: location
    tags: tags
    environmentId: platform.outputs.environmentId
    identities: identities.outputs.identities
    registryHost: registry.outputs.loginServer
    backendImage: backendImage
    databaseHost: data.outputs.databaseHost
    vaultUrl: data.outputs.vaultUrl
    bootstrapSecretJson: bootstrapSecretJson
  }
}
module runtime 'modules/runtime.bicep' = if (stage == 'runtime') {
  name: 'tactiq-runtime'
  scope: group
  params: {
    location: location
    tags: tags
    environmentId: platform.outputs.environmentId
    defaultDomain: platform.outputs.defaultDomain
    identities: identities.outputs.identities
    registryHost: registry.outputs.loginServer
    backendImage: backendImage
    webImage: webImage
    databaseHost: data.outputs.databaseHost
    blobUrl: data.outputs.blobUrl
    vaultUrl: data.outputs.vaultUrl
    namespaceHost: platform.outputs.namespaceHost
    queueName: platform.outputs.queueName
    microsoftClientId: microsoftClientId
    foundryProjectEndpoint: foundryProjectEndpoint
    foundryDeployment: foundryDeployment
    speechRegion: speechRegion
    budgetPeriodStart: budgetPeriodStart
    budgetPeriodEnd: budgetPeriodEnd
    speechEnabled: speechEnabled
    speechPrice: speechPrice
    speechVoicesJson: speechVoicesJson
    speechPairsJson: speechPairsJson
    activateJobs: activateJobs
    cvModelPath: cvModelPath
    cvModelSha256: cvModelSha256
    trustedProxyCidrs: trustedProxyCidrs
  }
}
output resourceGroup string = group.name
output registryHost string = registry.outputs.loginServer
output identities array = identities.outputs.identities
output databaseHost string = data.outputs.databaseHost
output vaultUrl string = data.outputs.vaultUrl
output publicOrigin string = 'https://ca-tactiq-web-3e4d.${platform.outputs.defaultDomain}'
output internalApiOrigin string = 'https://ca-tactiq-api-3e4d.internal.${platform.outputs.defaultDomain}'
output microsoftCallback string = 'https://ca-tactiq-web-3e4d.${platform.outputs.defaultDomain}/api/v1/auth/microsoft/callback'
