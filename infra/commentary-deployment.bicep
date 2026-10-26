targetScope = 'resourceGroup'

// A separate pay-as-you-go child leaves the shared deployment and account unchanged.
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: 'c0n2026-resource'
}

resource commentary 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'tactiq-commentary'
  sku: {
    name: 'GlobalStandard'
    capacity: 20
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.6-sol'
      version: '2026-07-09'
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

output deploymentName string = commentary.name
