@description('Approved region.')
param location string
@description('Resource tags.')
param tags object
@description('Delegated Container Apps subnet.')
param appsSubnetId string
@description('Worker identity, used for bounded queue delivery.')
param jobsIdentity object

resource logs 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: 'log-tactiq-prod-3e4d'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.05') }
    features: { enableLogAccessUsingOnlyResourcePermissions: true, disableLocalAuth: true }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-tactiq-prod-3e4d'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    DisableLocalAuth: true
    IngestionMode: 'LogAnalytics'
  }
}
resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'cae-tactiq-prod-3e4d'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: { destination: 'azure-monitor' }
    vnetConfiguration: { infrastructureSubnetId: appsSubnetId, internal: false }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    zoneRedundant: false
    peerTrafficConfiguration: { encryption: { enabled: true } }
  }
}
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'tactiq-redacted-logs'
  scope: environment
  properties: {
    workspaceId: logs.id
    logs: [
      { category: 'ContainerAppConsoleLogs', enabled: true }
      { category: 'ContainerAppSystemLogs', enabled: true }
    ]
    metrics: []
  }
}
resource broker 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: 'sbns-tactiq-prod-3e4d'
  location: location
  tags: tags
  sku: { name: 'Basic', tier: 'Basic' }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}
resource queue 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: broker
  name: 'analysis'
  properties: {
    lockDuration: 'PT1M'
    defaultMessageTimeToLive: 'P1D'
    deadLetteringOnMessageExpiration: true
    maxDeliveryCount: 3
    enablePartitioning: false
    requiresDuplicateDetection: false
    maxSizeInMegabytes: 1024
  }
}
var queueRoles = [
  '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
]
resource queueGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for roleId in queueRoles: {
  name: guid(queue.id, jobsIdentity.principalId, roleId)
  scope: queue
  properties: {
    principalId: jobsIdentity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
  }
}]
output environmentId string = environment.id
output defaultDomain string = environment.properties.defaultDomain
output namespaceHost string = '${broker.name}.servicebus.windows.net'
output queueName string = queue.name
