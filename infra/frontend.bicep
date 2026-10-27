targetScope = 'resourceGroup'

@description('Verified approved web repository SHA256 reference. Validate exact repository/digest before deployment.')
@minLength(71)
param webImage string
@minValue(0)
@maxValue(1)
param minReplicas int = 0

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: 'cae-tactiq-prod-3e4d'
}
resource webIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: 'id-tactiq-web-3e4d'
}
module web 'modules/app.bicep' = {
  name: 'tactiq-web-only'
  params: {
    name: 'ca-tactiq-web-3e4d'
    minReplicas: minReplicas
    location: 'westus3'
    tags: { application: 'tactiq', environment: 'prod-pilot', plan: 'tactiq-private-3e4d' }
    environmentId: environment.id
    identity: { id: webIdentity.id }
    registryHost: 'b8e2968c0n2026ca14acr.azurecr.io'
    image: webImage
    external: true
    port: 3000
    cpu: '0.25'
    memory: '0.5Gi'
    environmentVariables: [
      { name: 'NODE_ENV', value: 'production' }
      { name: 'PORT', value: '3000' }
      { name: 'HOSTNAME', value: '0.0.0.0' }
      { name: 'TACTIQ_BACKEND_URL', value: 'https://ca-tactiq-api-3e4d.internal.${environment.properties.defaultDomain}' }
    ]
    livePath: '/'
    readyPath: '/'
  }
}
output publicOrigin string = 'https://${web.outputs.fqdn}'
