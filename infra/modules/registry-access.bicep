@description('Existing registry name; this module never updates it.')
param registryName string
@description('New Tactiq identities from the identity module.')
param identities array

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: registryName
}
var readerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b93aa761-3e63-49ed-ac28-beffa264f7ac')
var writerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a1e307c-b015-4ebd-883e-5b7698a07328')
var writerCondition = '''
(
  (@Request[Microsoft.ContainerRegistry/registries/repositories:name] StringEqualsIgnoreCase 'tactiq-3e4d/backend')
  OR
  (@Request[Microsoft.ContainerRegistry/registries/repositories:name] StringEqualsIgnoreCase 'tactiq-3e4d/web')
)
'''

resource grants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for identity in identities: {
  name: guid(registry.id, identity.principalId, 'tactiq-repository-access-v1')
  scope: registry
  properties: {
    principalId: identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: identity.role == 'build' ? writerRoleId : readerRoleId
    description: 'Tactiq-only repository access. No catalog, delete, or registry administration.'
    conditionVersion: '2.0'
    // All actions in either assigned role are repository data actions.
    condition: identity.role == 'build' ? writerCondition : format('(@Request[Microsoft.ContainerRegistry/registries/repositories:name] StringEqualsIgnoreCase \'tactiq-3e4d/{0}\')', identity.role == 'web' ? 'web' : 'backend')
  }
}]
output loginServer string = registry.properties.loginServer
