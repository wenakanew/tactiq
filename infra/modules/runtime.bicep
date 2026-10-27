@description('Approved region.')
param location string
@description('Resource tags.')
param tags object
@description('VNet-integrated environment.')
param environmentId string
@description('Generated environment domain.')
param defaultDomain string
@description('New workload identities.')
param identities array
@description('Existing authenticated registry hostname.')
param registryHost string
@description('Digest-pinned backend image.')
param backendImage string
@description('Digest-pinned standalone frontend image built with output internalApiOrigin.')
param webImage string
@description('Private database hostname.')
param databaseHost string
@allowed(['managed_identity', 'password'])
param databaseAuth string = 'managed_identity'
@description('Private Blob account URL.')
param blobUrl string
@description('Private Key Vault URL.')
param vaultUrl string
@description('Service Bus namespace hostname.')
param namespaceHost string
@description('Service Bus queue name.')
param queueName string
@description('Microsoft Entra application ID, registered separately with exact callback.')
@minLength(36)
param microsoftClientId string
@allowed(['secret', 'certificate'])
param microsoftAuth string = 'secret'
@description('Existing configured Foundry project endpoint, never a newly created account.')
@minLength(10)
param foundryProjectEndpoint string
@description('Existing working model deployment name.')
@minLength(1)
param foundryDeployment string
@description('Existing configured Speech region.')
@minLength(1)
param speechRegion string
@description('Explicit fixed budget-period start epoch seconds; never automatically renewed.')
@minLength(1)
param budgetPeriodStart string
@description('Explicit fixed budget-period end epoch seconds.')
@minLength(1)
param budgetPeriodEnd string
@description('Speech remains off until exact voice prices and live results are approved.')
param speechEnabled bool = false
@description('Approved conservative microUSD price per million Speech characters when enabled.')
@minValue(1)
param speechPrice int = 30000000
@description('Verified locale-to-single-voice JSON; empty disables voices.')
param speechVoicesJson string = '{}'
@description('Verified locale-to-ordered-voice-pair JSON; empty disables dual voices.')
param speechPairsJson string = '{}'
@description('Activate scheduled/event jobs only after private bootstrap and runtime checks.')
param activateJobs bool = false
@description('Budget-balanced warm serving: one 0.25 vCPU / 0.5 GiB replica per app. Jobs are unchanged.')
param warmServing bool = false
@description('Licensed, digest-verified ONNX model packaged in the worker image; required before enabling CV uploads.')
param cvModelPath string = ''
@description('Verified packaged ONNX model sha256; no runtime model download.')
param cvModelSha256 string = ''
@description('Verified ingress proxy CIDRs only; never trust every forwarded address.')
@minLength(1)
param trustedProxyCidrs string

var apiIdentity = first(filter(identities, i => i.role == 'api'))
var jobsIdentity = first(filter(identities, i => i.role == 'jobs'))
var webIdentity = first(filter(identities, i => i.role == 'web'))
var publicOrigin = 'https://ca-tactiq-web-3e4d.${defaultDomain}'
var internalApiOrigin = 'https://ca-tactiq-api-3e4d.internal.${defaultDomain}'
var common = [
  { name: 'APP_ENV', value: 'production' }
  { name: 'TACTIQ_RUNTIME_MODE', value: 'cloud' }
  { name: 'TACTIQ_PUBLIC_ORIGIN', value: publicOrigin }
  { name: 'TACTIQ_CORS_ALLOW_ORIGINS', value: publicOrigin }
  { name: 'TACTIQ_TRUSTED_PROXY_CIDRS', value: trustedProxyCidrs }
  { name: 'TACTIQ_POSTGRES_AUTH', value: databaseAuth }
  { name: 'TACTIQ_POSTGRES_MAX_CONNECTIONS', value: '4' }
  { name: 'TACTIQ_IP_HMAC_KEY_HEX', secretRef: 'ip-hmac-key' }
  { name: 'TACTIQ_BLOB_ACCOUNT_URL', value: blobUrl }
  { name: 'TACTIQ_BLOB_CONTAINER', value: 'media' }
  { name: 'TACTIQ_CLOUD_ANALYSES_PER_DAY', value: '1' }
  { name: 'TACTIQ_CLOUD_JOB_CAPACITY', value: '1' }
  { name: 'VIDEO_UPLOAD_MAX_BYTES', value: '209715200' }
  { name: 'TACTIQ_SERVICEBUS_NAMESPACE', value: namespaceHost }
  { name: 'TACTIQ_SERVICEBUS_QUEUE', value: queueName }
  { name: 'TACTIQ_WORKER_SCRATCH_ROOT', value: '/scratch' }
  { name: 'TACTIQ_CV_DETECTOR', value: 'onnx' }
  { name: 'TACTIQ_CV_MODEL_PATH', value: cvModelPath }
  { name: 'TACTIQ_CV_MODEL_SHA256', value: cvModelSha256 }
  { name: 'TACTIQ_CV_MODEL_FORMAT', value: 'yolox' }
]
var microsoftSecretName = microsoftAuth == 'certificate' ? 'microsoft-client-certificate' : 'microsoft-client-secret'
var keyNames = concat(['ip-hmac-key', microsoftSecretName, 'foundry-api-key'], databaseAuth == 'password' ? ['postgres-api-dsn', 'postgres-ca-pem'] : [])
var caEnv = databaseAuth == 'password' ? [{ name: 'TACTIQ_POSTGRES_CA_PEM', secretRef: 'postgres-ca-pem' }] : []
var apiSecrets = [for name in concat(keyNames, speechEnabled ? ['speech-api-key'] : []): {
  name: name
  identity: apiIdentity.id
  keyVaultUrl: '${vaultUrl}secrets/${name}'
}]
var apiEnv = concat(common, caEnv, [
  { name: 'AZURE_CLIENT_ID', value: apiIdentity.clientId }
  databaseAuth == 'password' ? { name: 'TACTIQ_POSTGRES_DSN', secretRef: 'postgres-api-dsn' } : { name: 'TACTIQ_POSTGRES_DSN', value: 'host=${databaseHost} dbname=tactiq user=${apiIdentity.name} sslmode=verify-full sslrootcert=/etc/ssl/certs/ca-certificates.crt' }
  { name: 'TACTIQ_MICROSOFT_CLIENT_ID', value: microsoftClientId }
  { name: microsoftAuth == 'certificate' ? 'TACTIQ_MICROSOFT_CLIENT_CERTIFICATE_PEM' : 'TACTIQ_MICROSOFT_CLIENT_SECRET', secretRef: microsoftSecretName }
  { name: 'TACTIQ_MICROSOFT_REDIRECT_URI', value: '${publicOrigin}/api/v1/auth/microsoft/callback' }
  { name: 'TACTIQ_MICROSOFT_AUDIENCE', value: 'common' }
  { name: 'FOUNDRY_ENABLED', value: 'true' }
  { name: 'AZURE_FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
  { name: 'AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME', value: foundryDeployment }
  { name: 'AZURE_FOUNDRY_API_KEY', secretRef: 'foundry-api-key' }
  { name: 'FOUNDRY_COMMENTARY_USE_AGENT_FRAMEWORK', value: 'true' }
  { name: 'FOUNDRY_COMMENTARY_USE_DIRECT_AGENTS', value: 'false' }
  { name: 'TACTIQ_CLOUD_MODEL_ENABLED', value: 'true' }
  { name: 'TACTIQ_CLOUD_SPEECH_ENABLED', value: string(speechEnabled) }
  { name: 'TACTIQ_AI_BUDGET_PERIOD_START', value: budgetPeriodStart }
  { name: 'TACTIQ_AI_BUDGET_PERIOD_END', value: budgetPeriodEnd }
  { name: 'TACTIQ_AI_GLOBAL_LIMIT_MICROUSD', value: '6500000' }
  { name: 'TACTIQ_AI_TENANT_LIMIT_MICROUSD', value: '6500000' }
  { name: 'TACTIQ_MODEL_PRICE_MICROUSD_PER_MILLION_TOKENS', value: '30000000' }
], speechEnabled ? [
  { name: 'AZURE_SPEECH_API_KEY', secretRef: 'speech-api-key' }
  { name: 'AZURE_SPEECH_REGION', value: speechRegion }
  { name: 'AZURE_SPEECH_FORCE_REST', value: 'true' }
  { name: 'AZURE_SPEECH_DEFAULT_VOICE_BY_LANGUAGE_JSON', value: speechVoicesJson }
  { name: 'AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON', value: speechPairsJson }
  { name: 'TACTIQ_SPEECH_PRICE_MICROUSD_PER_MILLION_CHARACTERS', value: string(speechPrice) }
] : [])

module api 'app.bicep' = {
  name: 'tactiq-api'
  params: {
    name: 'ca-tactiq-api-3e4d'
    location: location
    tags: tags
    environmentId: environmentId
    identity: apiIdentity
    registryHost: registryHost
    image: backendImage
    external: false
    port: 8080
    cpu: warmServing ? '0.25' : '0.5'
    memory: warmServing ? '0.5Gi' : '1Gi'
    minReplicas: warmServing ? 1 : 0
    environmentVariables: apiEnv
    secrets: apiSecrets
    livePath: '/health/live'
    readyPath: '/health/ready'
  }
}
module web 'app.bicep' = {
  name: 'tactiq-web'
  params: {
    name: 'ca-tactiq-web-3e4d'
    minReplicas: warmServing ? 1 : 0
    location: location
    tags: tags
    environmentId: environmentId
    identity: webIdentity
    registryHost: registryHost
    image: webImage
    external: true
    port: 3000
    cpu: '0.25'
    memory: '0.5Gi'
    environmentVariables: [
      { name: 'NODE_ENV', value: 'production' }
      { name: 'PORT', value: '3000' }
      { name: 'HOSTNAME', value: '0.0.0.0' }
      { name: 'TACTIQ_BACKEND_URL', value: internalApiOrigin }
    ]
    livePath: '/'
    readyPath: '/'
  }
  dependsOn: [api]
}
var jobEnv = concat(common, caEnv, [
  { name: 'AZURE_CLIENT_ID', value: jobsIdentity.clientId }
  databaseAuth == 'password' ? { name: 'TACTIQ_POSTGRES_DSN', secretRef: 'postgres-jobs-dsn' } : { name: 'TACTIQ_POSTGRES_DSN', value: 'host=${databaseHost} dbname=tactiq user=${jobsIdentity.name} sslmode=verify-full sslrootcert=/etc/ssl/certs/ca-certificates.crt' }
  { name: 'TACTIQ_CLOUD_MODEL_ENABLED', value: 'false' }
  { name: 'TACTIQ_CLOUD_SPEECH_ENABLED', value: 'false' }
])
var definitions = [
  { role: 'dispatch', timeout: 30, cpu: '0.25', memory: '0.5Gi', trigger: 'Schedule', schedule: '*/15 * * * *', args: ['dispatch', '--once'] }
  { role: 'worker', timeout: 840, cpu: '1', memory: '2Gi', trigger: 'Event', schedule: '', args: ['worker', '--once'] }
  { role: 'maintain', timeout: 120, cpu: '0.25', memory: '0.5Gi', trigger: 'Schedule', schedule: '0 * * * *', args: ['cleanup'] }
]
module jobs 'job.bicep' = [for job in definitions: {
  name: 'tactiq-${job.role}'
  params: {
    name: 'job-${job.role}-tactiq-3e4d'
    location: location
    tags: tags
    environmentId: environmentId
    identity: jobsIdentity
    registryHost: registryHost
    image: backendImage
    command: concat(['python', '-m', 'tactiq.runtime.cloud_entrypoint'], job.args)
    environmentVariables: jobEnv
    secrets: [for name in concat(['ip-hmac-key'], databaseAuth == 'password' ? ['postgres-jobs-dsn', 'postgres-ca-pem'] : []): {
      name: name
      identity: jobsIdentity.id
      keyVaultUrl: '${vaultUrl}secrets/${name}'
    }]
    cpu: job.cpu
    memory: job.memory
    timeout: job.timeout
    trigger: activateJobs ? job.trigger : 'Manual'
    schedule: job.schedule
    namespaceHost: namespaceHost
    queueName: queueName
  }
}]
output publicOrigin string = publicOrigin
output internalApiOrigin string = internalApiOrigin
output apiFqdn string = api.outputs.fqdn
output webFqdn string = web.outputs.fqdn
