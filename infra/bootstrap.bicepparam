using './main.bicep'

param stage = 'bootstrap'
param backendImage = readEnvironmentVariable('TACTIQ_DEPLOY_BACKEND_IMAGE')
param bootstrapSecretJson = readEnvironmentVariable('TACTIQ_BOOTSTRAP_SECRETS_JSON')
