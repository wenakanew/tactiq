@description('Job name.')
param name string
@description('Approved region.')
param location string
@description('Resource tags.')
param tags object
@description('VNet-integrated Consumption environment.')
param environmentId string
@description('Job managed identity.')
param identity object
@description('Existing registry hostname.')
param registryHost string
@description('Digest-pinned backend image.')
@minLength(71)
param image string
@description('Entrypoint command.')
param command array
@description('Nonsecret environment values or secret references.')
param environmentVariables array
@description('Key Vault secret references.')
param secrets array
@description('CPU cores represented as JSON.')
param cpu string
@description('Memory allocation.')
param memory string
@description('Per-replica execution timeout, not a whole-subscription billing stop.')
param timeout int
@description('Trigger selection. Manual until live release verification.')
@allowed(['Manual', 'Schedule', 'Event'])
param trigger string
@description('Approved schedule, if scheduled.')
param schedule string = ''
@description('Service Bus hostname used for event scaling.')
param namespaceHost string
@description('Service Bus queue name.')
param queueName string

resource job 'Microsoft.App/jobs@2025-01-01' = {
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
    configuration: union({
      triggerType: trigger
      replicaRetryLimit: 0
      replicaTimeout: timeout
      registries: [{ server: registryHost, identity: identity.id }]
      secrets: secrets
    }, trigger == 'Schedule' ? {
      scheduleTriggerConfig: { cronExpression: schedule, parallelism: 1, replicaCompletionCount: 1 }
    } : trigger == 'Event' ? {
      eventTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
        scale: {
          minExecutions: 0
          maxExecutions: 1
          pollingInterval: 60
          rules: [
            {
              name: 'analysis'
              type: 'azure-servicebus'
              identity: identity.id
              metadata: {
                namespace: split(namespaceHost, '.')[0]
                queueName: queueName
                messageCount: '1'
              }
            }
          ]
        }
      }
    } : {
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
    })
    template: {
      containers: [
        {
          name: 'job'
          image: image
          command: command
          env: environmentVariables
          resources: { cpu: json(cpu), memory: memory }
          volumeMounts: [{ mountPath: '/scratch', volumeName: 'scratch' }]
        }
      ]
      volumes: [{ name: 'scratch', storageType: 'EmptyDir' }]
    }
  }
}
output id string = job.id
