using './main.bicep'

param stage = 'runtime'
param backendImage = readEnvironmentVariable('TACTIQ_DEPLOY_BACKEND_IMAGE')
param webImage = readEnvironmentVariable('TACTIQ_DEPLOY_WEB_IMAGE')
param microsoftClientId = readEnvironmentVariable('TACTIQ_MICROSOFT_CLIENT_ID')
param foundryProjectEndpoint = readEnvironmentVariable('AZURE_FOUNDRY_PROJECT_ENDPOINT')
param foundryDeployment = readEnvironmentVariable('AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME')
param budgetPeriodStart = readEnvironmentVariable('TACTIQ_AI_BUDGET_PERIOD_START')
param budgetPeriodEnd = readEnvironmentVariable('TACTIQ_AI_BUDGET_PERIOD_END')
param cvModelPath = readEnvironmentVariable('TACTIQ_CV_MODEL_PATH')
param cvModelSha256 = readEnvironmentVariable('TACTIQ_CV_MODEL_SHA256')
param trustedProxyCidrs = readEnvironmentVariable('TACTIQ_TRUSTED_PROXY_CIDRS')
param activateJobs = false
param speechEnabled = false
