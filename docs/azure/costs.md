# Cost boundaries

[Documentation](../README.md) · [Deployment gates](deployment.md)

## The budget is subscription-wide

The user-approved ceiling is **US$150/month for the entire subscription**, including other projects, hosting, data, AI, and Speech. It is not an allocation of $150 exclusively to Tactiq.

A read-only daily Cost Management check covering September 26-October 25 found that the October forecast understated the full recurring baseline. On the latest complete days (October 22-24), existing App Service, Standard Registry and Standard Static Web App billed approximately $1.518768, $0.666605 and $0.295890 per day respectively. At 730 hours/month this projects to **$75.47 fixed charges**, or **$75.76 including the previously observed $0.289105 shared AI usage once**. October 25 had incomplete billing and was excluded from the daily-rate projection. These are recent actual billed rates, not a future-price guarantee. No unused credit/grant is assumed. Do not delete/downsize other applications to make the proposal fit.

The owner approved reusing the existing registry for separate Tactiq images, fifteen-minute dispatch, the final pilot limits and subsequently risk-acknowledged additive provisioning. Managed PostgreSQL could not allocate the approved SKU in westus3. Under the owner's autonomous-reroute authorization, a private PostgreSQL 17 VM fallback was deployed in the same region, without opening database or SSH ingress. Its compute, disks and outbound address revise the whole-subscription projection from **$145.61 to $146.13/month**, including the **$6.50 shared model/Speech allocation**, leaving approximately **$3.87 margin** before unmeasured overages. This is a conditional planning estimate, not a final invoice or hard spending cap. The superseded separate-registry/five-minute version totaled $153.20 and must not be deployed.

Registry preflight confirmed that the existing Standard registry uses `AbacRepositoryPermissions`. Runtime identities need `Container Registry Repository Reader` and the build identity needs `Container Registry Repository Writer`, with conditions restricting every data action to the exact Tactiq repositories. `AcrPull`/`AcrPush` are not honored in this mode. Do not change registry mode, enable admin credentials, or grant unconditioned access. Backend and frontend Linux images are built and deployed from digest-pinned isolated repositories. Shared-registry storage and southafricanorth-to-westus3 image transfer remain variable costs.

## October 27 balanced warm serving profile

To avoid serial frontend/backend cold starts, the deployed web and API each retain one 0.25 vCPU / 0.5 GiB replica, maximum one. The API previously had 0.5 vCPU / 1 GiB with minimum zero; workers are unchanged. Current westus3 Consumption retail meters were rechecked: active CPU USD 0.000024/vCPU-second, idle CPU USD 0.000003/vCPU-second, and active/idle memory USD 0.000003/GiB-second. No free grant is assumed.

Using 730 hours/month, eight active hours/day and sixteen idle hours/day: previous serving allowance was USD 19.71/month; the balanced warm profile is USD 13.14 active plus USD 7.884 idle, totaling **USD 21.024/month**. This changes the conditional whole-subscription estimate from USD 146.13 to approximately **USD 147.44/month**, leaving about USD 2.56 before variable overages. Idle billing depends on Azure classifying the replica as idle; background activity or more than eight active hours/day increases costs. At continuous active rates, the same arithmetic would reach approximately USD 165.84 for the subscription, so this is not a hard spending guarantee.

The observed API working set after rollout was approximately 285-306 MiB, with no restarts in the verification window. The smaller profile is not certified for concurrent paid-provider workloads. Monitor memory, restarts and actual costs; if unsafe, revert to the former API resources and minimum zero rather than silently expanding the budget. The optional `warmServing` parameter in `infra/runtime-only.bicep` reproduces the profile; the generic template defaults remain scale-to-zero so always-on spend is not introduced accidentally. Frontend-only redeployment can retain minimum one via `minReplicas=1`.

Model operations settle complete SDK-reported token usage after durable results are saved, at the configured conservative maximum price of $30 per million tokens. Unknown usage, fallback/partial failures and Speech keep full reservations. Single-narrative headroom is calculated from its exact prompt bytes plus bounded completion/envelope allowance; dual commentary requires **$4.15176 free** before its two-role operation, conservatively reserving two times (64,000 input bytes + 1,100 completion tokens + 4,096 envelope tokens). An exhausted allocation returns an explicit error; deterministic explanations remain available. Identical completed operations reuse durable results, not another paid call.

Speech uses only the configured standard Neural voices in seven locales. Regional public retail was $15 per million characters at activation; the ledger conservatively reserves at **$30 per million**, including SSML/text expansion. This is not a verified billed-character receipt. Daily private database dumps have an eight-day Blob lifecycle deletion policy; accumulated backups, soft-deleted data and actual storage transactions still consume storage allowance.

The isolated `tactiq-commentary` deployment shares the existing account and uses Global Standard pay-as-you-go billing, not provisioned hourly throughput. Its 20,000-token/minute limit fixes the original shared deployment's 1,000-token/minute bottleneck without changing that deployment or increasing the $6.50 application allocation. Model throughput quota is not an additional spending allowance.

## Before approving resources

- Establish actual existing expenditure and forecast, billing currency, regional pricing, and capacity.
- Select a remaining Tactiq allocation including variable AI/Speech, processing, database, media/backup, transfer, telemetry, and registry costs.
- Bound request-serving replicas and separately bound expensive job concurrency/queue admission. “100 simultaneous users” does not mean 100 concurrent CV jobs.
- Use global and tenant/session request/usage reservations. Microsoft sign-in must not bypass spending controls.
- Account for replay/Listen usage: no additional trial charge does not mean unlimited paid model/audio generation.
- Record forecast assumptions, limits, alert recipients, and owner-approved response actions.

Azure Budget alerts are notifications, not a hard spending stop. Tactiq cannot stop charges generated by unrelated applications. In-process reference rate limits and caches are not distributed budget enforcement.

## Verification status

The web, internal API, private database fallback, broker, private storage/vault and scheduled/event jobs are deployed. A real synthetic analysis completed through the public workspace and cloud worker; database backup download and isolated restore verified all 22 application tables. See the [release record](deployment.md) for provider evidence and remaining qualifications. Actual future production spending and sustained-load costs are **not verified**. Existing billed rates can change, always-active serving can exceed the assumed eight hours/day, and shared-registry cross-region image transfer/storage must be measured. One-time bootstrap/probe compute is additional variable usage within the remaining margin, not a new always-on service. No enterprise SLA or multi-region availability commitment is included in this budget-sized target.

## Central US recovery assessment (not approved or provisioned)

Read-only retail checks found the same Central US Consumption compute rates ($0.000024/vCPU-second and $0.000003/GiB-second), Hot LRS storage/read/write rates ($0.0184/GB-month, $0.004/10K reads, $0.05/10K writes), Basic messaging ($0.05/million operations), standard vault operations ($0.03/10K) and Analytics Logs data analyzed ($2.30/GB) as the approved assumptions. Global Standard Private Endpoint remains $0.01/hour; private DNS first-tier zones/queries remain $0.50/zone and $0.40/million queries. Source: Azure Retail Prices API, Consumption meters. These checks are not a capacity guarantee or a complete future bill.

Central US B1ms costs $0.01921/hour plus $0.13/GB-month for standard Flexible Server storage: **$18.1833 for 730 hours and 32GB**, versus westus3's $16.09. Keeping all other approved allowances unchanged gives a **conditional $147.706177 whole-subscription monthly projection**, leaving **$2.293823**, not a safe hard-cap guarantee. Internet/cross-region transfer, registry build/storage usage, remaining regional differences and partial-resource overlap are not newly verified by this arithmetic.

Do not move only the private delegated database without designing cross-region connectivity, or keep duplicate endpoints running indefinitely. A regional replacement would require the application and private data network together, an approved lifecycle for the partial westus3 resources, and a reviewed new preview. No replacement region, cleanup, shared-image deletion or deletion of unrelated resources is authorized by this assessment.
