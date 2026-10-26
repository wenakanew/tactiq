@description('Approved application region.')
param location string
@description('Existing registry region for the dedicated build identity.')
param buildLocation string
@description('Resource tags.')
param tags object

var roles = ['api', 'jobs', 'web', 'bootstrap', 'build']
resource identities 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = [for role in roles: {
  name: 'id-tactiq-${role}-3e4d'
  location: role == 'build' ? buildLocation : location
  tags: tags
}]
output identities array = [for (role, i) in roles: {
  role: role
  name: identities[i].name
  id: identities[i].id
  clientId: identities[i].properties.clientId
  principalId: identities[i].properties.principalId
}]
