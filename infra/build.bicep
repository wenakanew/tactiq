targetScope = 'resourceGroup'

@description('Actual internal API origin from the existing westus3 environment, baked into Next.js rewrites.')
param internalApiOrigin string

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: 'b8e2968c0n2026ca14acr'
}
resource buildIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  scope: resourceGroup('rg-tactiq-prod-3e4d')
  name: 'id-tactiq-build-3e4d'
}
var roles = ['backend', 'web']
resource tasks 'Microsoft.ContainerRegistry/registries/tasks@2025-03-01-preview' = [for role in roles: {
  parent: registry
  name: 'tactiq-3e4d-${role}-build'
  location: 'southafricanorth'
  tags: { application: 'tactiq', plan: 'tactiq-private-3e4d' }
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${buildIdentity.id}': {} } }
  properties: {
    status: 'Enabled'
    timeout: 1800
    agentConfiguration: { cpu: 2 }
    platform: { os: 'Linux', architecture: 'amd64' }
    credentials: {
      sourceRegistry: { loginMode: 'Default', identity: buildIdentity.properties.clientId }
    }
    step: {
      type: 'Docker'
      contextPath: role == 'web' ? 'https://github.com/wenakanew/tactiq.git#master:apps/web' : 'https://github.com/wenakanew/tactiq.git#master'
      dockerFilePath: 'Dockerfile'
      imageNames: ['${registry.name}.azurecr.io/tactiq-3e4d/${role}:release-{{.Run.ID}}']
      isPushEnabled: true
      noCache: false
      arguments: role == 'web' ? [{ name: 'TACTIQ_BACKEND_URL', value: internalApiOrigin, isSecret: false }] : []
    }
    trigger: {
      baseImageTrigger: { name: 'base-image', baseImageTriggerType: 'Runtime', status: 'Disabled' }
      sourceTriggers: []
      timerTriggers: []
    }
  }
}]
