@description('Application resource name.')
param name string
@description('Approved region.')
param location string
@description('Resource tags.')
param tags object
@description('VNet-integrated Consumption environment.')
param environmentId string
@description('Runtime managed identity.')
param identity object
@description('Existing authenticated registry hostname.')
param registryHost string
@description('Immutable image reference including sha256 digest.')
@minLength(71)
param image string
@description('Only the frontend has public ingress.')
param external bool
@description('Container listening port.')
param port int
@description('CPU cores represented as JSON to preserve fractional values.')
param cpu string
@description('Container memory.')
param memory string
@description('Nonsecret values or secret references, never raw credentials.')
param environmentVariables array
@description('Versionless Key Vault secret references.')
param secrets array = []
@description('Liveness path.')
param livePath string
@description('Readiness path.')
param readyPath string
@description('Keep one serving replica only under the reviewed warm-profile budget.')
@minValue(0)
@maxValue(1)
param minReplicas int = 0

resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      maxInactiveRevisions: 1
      ingress: {
        external: external
        allowInsecure: false
        targetPort: port
        transport: 'auto'
        traffic: [{ latestRevision: true, weight: 100 }]
      }
      registries: [{ server: registryHost, identity: identity.id }]
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'app'
          image: image
          env: environmentVariables
          resources: { cpu: json(cpu), memory: memory }
          probes: [
            {
              type: 'Startup'
              httpGet: { path: livePath, port: port, scheme: 'HTTP' }
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 30
            }
            {
              type: 'Liveness'
              httpGet: { path: livePath, port: port, scheme: 'HTTP' }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: readyPath, port: port, scheme: 'HTTP' }
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1
        cooldownPeriod: 60
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '10' } } }
        ]
      }
    }
  }
}
output fqdn string = app.properties.configuration.ingress.fqdn
output id string = app.id
