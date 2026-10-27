# Live code refresh and workspace latency

Status: Validated

## 1. Scope

Update the existing Tactiq pilot in place with the merged judge experience and validated workspace-startup improvements. Reuse the current subscription, westus3 resource group, identities, private network, registry, database, storage and jobs. No new analysis, paid model calls, quota resets or data migrations.

## 2. Azure context

Existing subscription: b8e29685-f42f-47d8-ac45-31adff4a18cf.
Existing resource group: rg-tactiq-prod-3e4d, westus3.
Recipe: AZCLI existing Container Apps image updates, not azd provisioning.

## 3. Preparation

- Inspect the current source, build procedure and live configuration.
- Share the capability response between session initialization and the workspace without caching session authorization.
- Ensure the production image includes the public example JSON and audio; the current Dockerfile omits `public`, and the staging allowlist omits audio.
- Proposed balanced warm profile: one frontend and one API replica, each 0.25 vCPU / 0.5 GiB. Keep jobs unchanged.
- Observed API working set was 253-260 MiB during the read-only sample. This is not a load/paid-provider peak; monitor deployment memory and revert the profile if unsafe.
- Current retail rates: active CPU USD 0.000024/vCPU-second, idle CPU USD 0.000003/vCPU-second, memory USD 0.000003/GiB-second. At the existing eight active hours/day assumption over 730 hours, serving changes from USD 19.71 to USD 21.024/month. Conditional whole-subscription estimate: USD 147.444/month, not a hard cap. No unused grants assumed.

## 4. Execution

- Validate application changes and immutable image builds.
- Update existing image references only, preserving private access and budget controls.
- Apply scaling changes only when approved and budget-compatible.

## 5. Verification

Check live example, fixture, entry link, workspace initialization, repeated timings, revision health and source/image provenance. Retain rollback image references.

## 6. Approval

User requested live code refresh and faster workspace on 2026-10-27. The clarification tool returned that the user is unavailable and instructed autonomous good decisions. Proceed with the balanced warm profile within the existing conditional USD 150 ceiling, retaining rollback to the original API sizing and scale-to-zero. No separate explicit acceptance of the proposed profile was received. Existing subscription and westus3 resources remain unchanged.

## 7. Validation Proof

- All validation checks pass:
  - Core AZCLI authentication, Bicep compilation, frontend template validation and what-if completed successfully on 2026-10-27. The what-if has property/default drift; do not apply the full frontend template. Deploy with image/resource/scale-only updates to preserve the existing configuration.
  - Azure Policy assignment query returned no assignments at subscription scope.
  - Frontend unit tests: 25 passed. Production build and type checks passed.
  - Focused backend/packaging/infrastructure/grounding regressions: 126 passed.
  - Production browser example/workspace/trial tests: 32 passed, including exactly one initial capabilities request and disabled unapproved analysis.
  - Checkov compiled frontend ARM scan exited 0; existing registry ABAC Reader/Writer conditions reviewed, no RBAC changes.
  - Remote frontend Linux build dfd succeeded, digest sha256:f358029f5ae254d9e7d33255255bd110ff029ba008288c2a12e08c83a93e98e6.
  - Remote backend Linux build dfe succeeded in 3m49s, digest sha256:9d9705dd3df6d3d6acd5d50cdbc5c8f96762ba71b28afa6db45cc9c36144f1ab. Initial dfb/dfc builds failed before deployment: CLI Dockerfile override resolved incorrectly and Debian HTTP downloads were inaccessible. Corrected by absolute staged contexts without `--file` and bounded HTTPS package acquisition. No failed image was deployed.
  - Validation workflow uses a session-local compatibility copy: Windows PowerShell 5 lacks the four-argument Enum.TryParse overload. Only enum parsing was adapted; all checks, proof and status steps are retained.
  - Commands: `npm test`; `npm run build`; `npx playwright test example.spec trial.spec studio.spec`; `.venv\Scripts\python.exe -m pytest` with the five targeted test files; `az bicep build` on runtime-only and frontend; the skill's AZCLI validation helper against frontend; `.venv\Scripts\python.exe -x .venv\Scripts\checkov.cmd -f infra\frontend.json --quiet --compact --framework arm`; `az policy assignment list`; `az acr task show-run` for dfd/dfe.

## 8. Rollback

Web: tactiq-3e4d/web@sha256:070df89ad3d785b30dafb7f734da025c3a9e90e7d9f45b9b2dc127bd780beac5, 0.25 vCPU / 0.5 GiB, min 0 / max 1.
API/jobs: tactiq-3e4d/backend@sha256:526e261fc72297d5d96f0d4cbcab250236eadb717eed80c79be7d876f7792ffd.
API rollback resources: 0.5 vCPU / 1 GiB, min 0 / max 1. Job resources and scheduling remain unchanged.

## 9. Execution and live verification

Deployed 2026-10-27: web revision 0000002, API revision 0000006; both Healthy with one replica. Maintenance, dispatch and worker image updates succeeded without starting a new analysis. Registry Reader/Writer conditions were verified unchanged for all four identities.

Live unmocked Chromium check: homepage example link visible; prepared example usable in 1740 ms, no API requests; Arabic recap supported; public JSON/WAV/MP3 downloaded byte-identically to local assets. Three fresh workspace contexts reached guest access in 3554, 3528 and 3976 ms; each made one capabilities request and one guest initialization, with no analysis/provider call. Landing usable in 1761 ms. Repeated HTTP first-byte times were approximately 1.0-1.23 seconds. API memory samples reached 321323008 bytes, with zero replica restarts.

Source manifests: web 92d00fa1a1cda3cb131655ddaa9733c57b63f448a1b6d750a682170dad845421; backend 9a4df68533f7ab774c9e3d24a96d5863e0d3e7e6d5266164e4be7df31d70f610.

No secrets, cookies or private analysis output are retained in the public record. The local verification receipt and screenshots are ignored deployment artifacts.
