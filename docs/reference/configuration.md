# Configuration

[Documentation](../README.md) · [Local development](../development/getting-started.md)

[Root environment example](../../.env.example) and [web example](../../apps/web/.env.example) are the maintained setting templates. Keep credentials in ignored local configuration or an approved secret store, never in documentation or screenshots. Existing private environment files must not be overwritten.

## Runtime and access

| Settings | Meaning |
| --- | --- |
| `APP_ENV`, `TACTIQ_AUTH_ENABLED` | Local versus production guards; local is not public authentication |
| `TACTIQ_API_TOKENS_JSON` | Digest-only tenant/subject/role entries; see [access](../operations/privacy-and-security.md) |
| `TACTIQ_COOKIE_SECURE`, `TACTIQ_AUTH_COOKIE_NAME` | Production requires secure session cookies |
| `TACTIQ_CORS_ALLOW_ORIGINS` | Explicit allowed browser origins; use HTTPS in production |
| `TACTIQ_MEDIA_DIR`, `TACTIQ_DATABASE_PATH` | Private local media and SQLite paths |
| `TACTIQ_MAX_CACHED_MATCHES` | Reference cache bound; default 32 |
| `TACTIQ_JOB_WORKERS`, `TACTIQ_JOB_CAPACITY` | Reference job bounds; defaults 2 workers / 8 pending+running |
| `TACTIQ_REQUESTS_PER_MINUTE`, `TACTIQ_EXPENSIVE_REQUESTS_PER_MINUTE` | Reference admission/rate controls, not distributed billing protection |
| `TACTIQ_BACKEND_URL` | Next server-side same-origin proxy target |
| `NEXT_PUBLIC_API_BASE_URL` | Deliberate direct-origin alternative |

Use the documented Uvicorn entry point; legacy `TACTIQ_DEV_BACKEND_HOST/PORT` and `TACTIQ_PROBE_*` entries, if still present in private configuration, are not required after retiring the root scripts.

## Video and CV

Uploads default to 200 MiB via `VIDEO_UPLOAD_MAX_BYTES`. `TACTIQ_VIDEO_MAX_REDIRECTS` bounds checked redirects. File downloads use size/deadline limits; these are not permission to access protected footage.

Configure `TACTIQ_CV_DETECTOR=onnx`, `TACTIQ_CV_MODEL_PATH`, `TACTIQ_CV_MODEL_FORMAT`, `TACTIQ_CV_INPUT_SIZE`, and person/ball class IDs. Supported formats: `yolox` raw grid, `yolox-decoded`, and `yolov8`. Production requires `TACTIQ_CV_MODEL_SHA256`. The `motion` detector is local-development only.

Reference smoke artifact: [YOLOX-tiny ONNX](https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx), format `yolox`, input 416, COCO person 0 / ball 32. SHA-256:

```text
427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7
```

Review the [upstream license](https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE) and artifact terms independently; store weights outside Git. The application never downloads weights. A successful inference smoke check is not football-accuracy validation.

`TACTIQ_VALUE_MODEL_PATH` selects a versioned value artifact. Local geometric reference estimates are labelled unvalidated and are not enabled as a production model.

## Foundry and Speech

`FOUNDRY_ENABLED=true`, `AZURE_FOUNDRY_PROJECT_ENDPOINT`, and `AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME` enable the configured model path. `DefaultAzureCredential`/managed identity is preferred; the model adapter also supports `AZURE_FOUNDRY_API_KEY`. Profile lookup uses identity separately from key-auth inference.

The `FOUNDRY_COMMENTARY_*` role names/instructions/version controls are profiles, not managed agent sessions. See [workflow](../architecture/agent-workflow.md).

`FOUNDRY_COMMENTARY_USE_AGENT_FRAMEWORK=true` selects the SDK workflow rather than the established custom path. Its profiles are local configuration/built-in versions; the legacy `FOUNDRY_COMMENTARY_USE_DIRECT_AGENTS` profile-retrieval behavior is not a hosted-session guarantee and must not be conflated with the Framework path. The metered cloud runtime always uses the bounded Framework path for dual commentary. Live target-model execution remains a release gate.

Speech uses `AZURE_SPEECH_API_KEY` with endpoint and/or region. Configure a single voice or two commentator voices and optional per-language maps:

- `AZURE_SPEECH_DEFAULT_VOICE` / `AZURE_SPEECH_DEFAULT_VOICE_BY_LANGUAGE_JSON`
- `AZURE_SPEECH_DEFAULT_COMMENTATOR_A_VOICE` / `B_VOICE`
- `AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON`
- Explicit role overrides `AZURE_SPEECH_COMMENTATOR_A_VOICE` / `B_VOICE`

Two role voices suffice for dual synthesis; the lead can narrate single text without a dedicated default. Explicit request voices override defaults; matching-language maps override global defaults. Incompatible non-multilingual voices fail rather than silently changing spoken language.

`NEXT_PUBLIC_ENABLE_CLOUD_SPEECH` is a build-time UI kill switch. Browser fallback uses matching-language voices; dual fallback requires two distinct identifiable male voices. Locale capability flags do not prove service health.

## Distributed cloud runtime

`TACTIQ_RUNTIME_MODE=cloud` selects `tactiq.presentation.api.entrypoint:app`; local remains the default. Run explicit migrations before starting a cloud API or worker. The image uses this mode-selecting entrypoint and preserves socket peers for verified proxy/IP quota handling.

| Settings | Cloud requirement |
| --- | --- |
| `TACTIQ_PUBLIC_ORIGIN` | Exact HTTPS web origin, without a trailing slash or path |
| `TACTIQ_POSTGRES_DSN` | Private database connection with verified TLS in Azure |
| `TACTIQ_POSTGRES_AUTH` | `password` (existing default) or `managed_identity`; identity mode obtains a fresh Entra token for each new connection and requires a password-free Azure PostgreSQL DSN with `sslmode=verify-full` and a provisioned database principal |
| `TACTIQ_POSTGRES_CA_PEM` | Optional private CA PEM for password-authenticated `sslmode=verify-full` connections; held in Key Vault in the deployed VM fallback, materialized only in a restricted temporary file while opening the connection |
| `TACTIQ_POSTGRES_MAX_CONNECTIONS` | Shared connection gate per process, default 4, maximum 16 |
| `TACTIQ_IP_HMAC_KEY_HEX` | Stable secret containing at least 32 random bytes encoded as hex |
| `TACTIQ_TRUSTED_PROXY_CIDRS` | Explicit actual ingress networks; never a universal trust range |
| `TACTIQ_CLOUD_ANALYSES_PER_DAY` | Approved positive processing allocation, or `disabled`; no implicit allocation |
| `TACTIQ_CLOUD_JOB_CAPACITY` | Maximum pending/running native analyses; not a connected-user limit |
| `TACTIQ_BLOB_ACCOUNT_URL`, `TACTIQ_BLOB_CONTAINER` | Existing private container; managed identity must have the scoped data permissions |
| `TACTIQ_SERVICEBUS_NAMESPACE`, `TACTIQ_SERVICEBUS_QUEUE` | Existing queue hostname/name for worker and outbox dispatcher |
| `TACTIQ_WORKER_SCRATCH_ROOT` | Dedicated disposable directory in the worker process |
| `AZURE_CLIENT_ID` | Optional user-assigned managed identity client ID |
| `TACTIQ_MICROSOFT_CLIENT_ID/SECRET/REDIRECT_URI` | Registered server-side Microsoft sign-in configuration; fixed callback `/api/v1/auth/microsoft/callback` |
| `TACTIQ_MICROSOFT_CLIENT_CERTIFICATE_PEM` | Alternative to `TACTIQ_MICROSOFT_CLIENT_SECRET`: matching RSA private key and current certificate, at least 2048 bits. Configure exactly one credential. The deployed pilot uses a 90-day certificate because tenant policy forbids client secrets; rotate before expiry. Never expose this PEM to the browser or logs. |
| `TACTIQ_MICROSOFT_AUDIENCE` | `common`, `organizations`, `consumers`, or an approved tenant |

The separate dispatcher must run even when workers are scaled to zero. It performs bounded database recovery before dispatch, including with `dispatch --once`, so an empty broker queue does not prevent expired work from being rediscovered. A continuous dispatcher repeats recovery at most once per minute; each one-shot invocation runs it once. Recovery failures propagate rather than reporting successful dispatch. Price its continuous or scheduled execution. A local DB/emulator check does not certify Azure identity, networking or container readiness.

Cloud composition limits execution to one unexpired database job lease across all tenants. Claim transactions serialize capacity checks before consuming attempts; idle or duplicate workers cannot acquire a second active lease. This is a logical execution bound, not an Azure billing cap or a guarantee that a stale native process stops immediately after losing its lease. Platform replica/execution limits, timeouts, revision overlap and fenced checkpoints remain necessary. Independent `PostgresStore` consumers keep their existing unconstrained behavior unless an explicit `max_running` is supplied.

### Paid cloud capability switches

Cloud flags are **not hard-coded false**. They depend on `TACTIQ_CLOUD_MODEL_ENABLED` / `TACTIQ_CLOUD_SPEECH_ENABLED`, actual provider configuration and an active, explicitly approved application allocation. `provider_health_verified=false` remains honest: configuration is not proof that a provider accepted a request.

Both switches default to false. To enable either, the operator must supply:

| Setting | Units / meaning |
| --- | --- |
| `TACTIQ_AI_BUDGET_PERIOD_START`, `TACTIQ_AI_BUDGET_PERIOD_END` | Fixed UTC epoch seconds shared by all replicas |
| `TACTIQ_AI_GLOBAL_LIMIT_MICROUSD` | Approved AI allocation across all Tactiq tenants; 1 USD = 1,000,000 microUSD |
| `TACTIQ_AI_TENANT_LIMIT_MICROUSD` | Per-workspace allocation, not greater than the global allocation |
| `TACTIQ_MODEL_PRICE_MICROUSD_PER_MILLION_TOKENS` | Approved worst-case token price, at least the higher input/output price for the exact deployment; required when model generation is enabled |
| `TACTIQ_SPEECH_PRICE_MICROUSD_PER_MILLION_CHARACTERS` | Approved price for the configured Speech voices/tier; required when speech is enabled |

These are server-only values, never browser estimates. Do not use the full US$150 subscription ceiling as the AI allocation: hosting, existing applications, telemetry, storage and networking also consume it. No pricing or billable verification is authorized by an example configuration.

Single narratives reserve the UTF-8 byte length of the exact system/user prompts, plus 1,600 completion tokens and 4,096 tokens of tool-free chat-envelope margin. They retain a 64 KB input bound. Dual commentary reserves 138,392 tokens: two times (64,000 input bytes + 1,100 completion tokens + 4,096 envelope tokens). Framework roles each enforce the serialized input bound including instructions, output schema and validated handoff; one UTF-8 byte conservatively bounds one input token. No tools, conversation history or paid retries are permitted on this path. Speech reserves an upper bound including text expansion/SSML and makes one HTTP attempt to one endpoint.

Foundry SDK calls have a 60-second deadline with no automatic paid transport retries; the two-role Framework workflow has a 130-second overall deadline. The Next.js server-side proxy allows 180 seconds so a scale-to-zero API cold start does not hit the default shorter proxy timeout. These are deadlines, not latency promises or authorization to repeat an uncertain provider call.

Successful model operations settle **after durable script and result persistence**, using validated SDK input/output/total token counts from every expected call (one narrative or both Framework roles). Accounting is request-scoped, including the synchronous/async thread bridge; generated text and audit metadata cannot authorize refunds. Settlement rounds up total tokens times the configured conservative maximum token price, not separate input/output retail prices. Cached results never repeat a paid call or settlement. Settlement errors propagate; if persistence succeeded but settlement failed, a later cached response is safe but the full reservation can remain stranded.

Fallbacks, partial workflows, invalid/missing usage and failed/ambiguous outcomes retain their worst-case reservations until the fixed period ends. Speech also retains its conservative reservation because the adapter has no verified billed-character receipt. An overrun is reported explicitly, never silently settled down. The capability reports `usage_accounting="reserved-with-verified-model-settlement"` while the allocation is active. This ledger is a spending guard, **not actual Azure billing**. Do not manually release an ambiguous reservation and retry it as if the provider never ran. Each new call still needs enough unused allocation for its entire initial reservation.

Generated scripts/results are persisted under the owning analysis. Identical requests reuse completed results across API replicas/restarts. An in-progress or ambiguous paid request returns an explicit unavailable response rather than repeating a charge. Cloud speech accepts only `{ "match_id": "...", "script_id": "..." }`; language, text, role order and voices come from the owned generated script and server configuration. The local runtime keeps its existing text-based contract.

## Profile maintenance

The supported [profile utility](../../tools/manage_commentary_profiles.py) reads only its explicit environment file or process environment:

```powershell
.\.venv\Scripts\python.exe tools\manage_commentary_profiles.py --env-file .env
```

Default execution validates configuration and prints a **dry-run** plan without credentials, SDK import, or network access. Mutation requires separately authorized `--apply --confirm-project "<exact configured HTTPS project endpoint>"`. Do not run mutation during setup, tests, or release cleanup. Only the two configured role names are targeted; no listing/deletion occurs. A partial failure returns nonzero and may leave one created version; inspect those exact roles before an authorized retry.

## Monitoring

`TACTIQ_MONITOR_API_URL`, `TACTIQ_MONITOR_ACCESS_TOKEN`, and `TACTIQ_ALERT_WEBHOOK_URL` configure the opt-in alert watcher. The token is administrator-scoped; the webhook must use HTTPS. See the [runbook](../operations/runbook.md).
