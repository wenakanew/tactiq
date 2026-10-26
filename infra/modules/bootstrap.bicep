@description('Approved region.')
param location string
@description('Resource tags.')
param tags object
@description('VNet-integrated environment.')
param environmentId string
@description('New Tactiq identities.')
param identities array
@description('Existing registry hostname.')
param registryHost string
@description('Digest-pinned backend image, already built and verified.')
@minLength(71)
param backendImage string
@description('Private database hostname.')
param databaseHost string
@allowed(['managed_identity', 'password'])
param postgresAuth string = 'managed_identity'
@description('Private Key Vault URL.')
param vaultUrl string
@description('Secret map delivered as an encrypted Container Apps secret, never a deployment output.')
@secure()
param bootstrapSecretJson string

var identity = first(filter(identities, i => i.role == 'bootstrap'))
var runtimeIdentities = filter(identities, i => contains(['api', 'jobs'], i.role))
resource bootstrap 'Microsoft.App/jobs@2025-01-01' = {
  name: 'job-bootstrap-tactiq-3e4d'
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      replicaRetryLimit: 0
      replicaTimeout: 300
      registries: [{ server: registryHost, identity: identity.id }]
      secrets: [{ name: 'bootstrap-input', value: bootstrapSecretJson }]
    }
    template: {
      containers: [
        {
          name: 'bootstrap'
          image: backendImage
          command: ['python', '-c', loadTextContent('../scripts/bootstrap.py')]
          env: [
            { name: 'AZURE_CLIENT_ID', value: identity.clientId }
            { name: 'TACTIQ_BOOTSTRAP_LOGIN', value: identity.name }
            { name: 'TACTIQ_BOOTSTRAP_DATABASE_HOST', value: databaseHost }
            { name: 'TACTIQ_BOOTSTRAP_POSTGRES_AUTH', value: postgresAuth }
            { name: 'TACTIQ_BOOTSTRAP_VAULT_URL', value: vaultUrl }
            { name: 'TACTIQ_BOOTSTRAP_PRINCIPALS', value: string(runtimeIdentities) }
            { name: 'TACTIQ_BOOTSTRAP_INPUT', secretRef: 'bootstrap-input' }
          ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
    }
  }
}
output jobName string = bootstrap.name
