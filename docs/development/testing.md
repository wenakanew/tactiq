# Testing and evidence

[Documentation](../README.md) · [Getting started](getting-started.md) · [Judging guide](../submission/judging-guide.md)

## Regression commands

Run from the repository root with development dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check services\backend\tactiq tests
.\.venv\Scripts\python.exe -m bandit -r services\backend\tactiq -ll -q
.\.venv\Scripts\python.exe -m tactiq.evaluation smoke --output .artifacts\quality-smoke.json
npm --prefix apps\web test
npm --prefix apps\web run typecheck
npm --prefix apps\web run build
npm --prefix apps\web run test:e2e
```

Start with the smallest relevant selectors when editing code. Browser tests require Chromium (`npx playwright install chromium` in `apps\web`) and FFmpeg. Fixtures use isolated local ports and synthetic scenes.

The [judge-experience verification report](judge-experience.md) documents the prepared `/example` package, timestamp and seven-locale reproductions, conservative localized-prose policy, actual browser evidence, first-use measurement and exact commands. Its example route has no backend dependency, although the shared browser suite starts a local backend to check the original analysis flow too.

For production-bundle browser checks, build with `TACTIQ_BACKEND_URL=http://127.0.0.1:18765` and `NEXT_PUBLIC_ENABLE_CLOUD_SPEECH=true`, then set `TACTIQ_E2E_PRODUCTION=true` for the browser run. Do not reuse a bundle built for a separate preview backend: public settings and rewrites are build-time values. Do not run `next dev`, builds or type-generation concurrently against the same `.next` directory. Avoid native/large database test suites during timing-sensitive playback checks on small shared hosts. These tests do not verify live Azure credentials. Rebuild with the intended backend origin before running a separate preview.

Real-scenario browser journeys allow up to 90 seconds for completion and 150 seconds for the whole journey. On the shared Windows validation host, isolated scenario reduction took about 21 seconds before Chromium/rendering overhead; profiling identified repeated rolling-window metric computation as the main cost. Browser traces showed continuing replay progress, rather than a stalled stream, at the old deadline. These bounds prevent functional checks from becoming accidental hardware benchmarks. They do not certify a production latency or concurrency target; measure those separately on the deployed worker configuration.

## Endpoint and provider checks

```powershell
$env:FOUNDRY_ENABLED="false"
$env:TACTIQ_AUDIT_REPORT_PATH=".artifacts\endpoint-matrix.json"
.\.venv\Scripts\python.exe -m pytest tests\system -q
.\.venv\Scripts\python.exe tools\check_processing.py --output .artifacts\processing.json
```

The full offline endpoint matrix requires positive coverage of every declared operation. A filtered test pass is not the whole-inventory gate. Model/Speech/CV fixtures are labelled boundaries, not live-provider proof. The processing diagnostic measures reduction/detection only, excluding decode, inference, storage, cloud, and network.

Only after explicit approval for billable calls:

```powershell
.\.venv\Scripts\python.exe tools\check_live_services.py --env-file .env --output-directory .artifacts\live-checks --allow-billable
```

This supported diagnostic replaces the root Speech probes. It records sanitized provider/audio evidence and stops further model operations after authentication/deployment/quota failures. A fallback HTTP 200 is not live-model success. Unit coverage includes voice configuration, ordered SSML/rendering, and failure behavior.

Optional real ONNX integration requires `TACTIQ_TEST_ONNX_MODEL` and a separately licensed `TACTIQ_TEST_FOOTBALL_CLIP`; do not depend on retired tracked media. These local assets are not distributed with the repository.

## Cloud contracts and real PostgreSQL

The optional cloud API is selected explicitly with `TACTIQ_RUNTIME_MODE=cloud`; the default local API is unchanged. PostgreSQL/Blob, queue, identity/trial, usage reservations and owned generation are wired to that cloud composition. Unit tests use explicit adapters/test doubles. Agent Framework tests execute the installed SDK with an offline model transport; they are not live inference evidence.

The `cloud-contracts` CI job runs disposable loopback-only PostgreSQL 17 and Microsoft Azurite services. Its fixed test credentials belong only to those ephemeral containers and must never be used for a deployment. The job has no Azure credentials and does not provision cloud resources. A workflow definition is not a passing hosted run; retain the actual run/artifact URL after authorized publication.

For an already approved disposable local database:

```powershell
# Supply a private DSN through local process configuration, never source control.
$env:TACTIQ_ALLOW_POSTGRES_TEST_WRITES="1"
.\.venv\Scripts\python.exe -m pytest tests\integration\test_cloud_postgres_integration.py -q
```

`TACTIQ_TEST_POSTGRES_DSN` must name the disposable test database. Tests create and drop only unique test schemas. Never point them at live application data. Missing approval or connection configuration produces an explicit skip, not verified PostgreSQL behavior.

Blob emulator integration uses `TACTIQ_ALLOW_BLOB_EMULATOR_WRITES=1` with `TACTIQ_TEST_BLOB_ACCOUNT_URL`, `TACTIQ_TEST_BLOB_ACCOUNT_NAME`, and `TACTIQ_TEST_BLOB_ACCOUNT_KEY` supplied privately by the local emulator configuration. It rejects non-loopback targets and creates/deletes only a unique test container. These tests use a compatible Storage API version and prove actual SDK byte/range/ETag behavior against Azurite—not deployed managed-identity/RBAC correctness.

## Independent quality gates

The [evaluation CLI](../../services/backend/tactiq/evaluation/__main__.py) supports tracking, independent calibration landmarks, tactical windows, narrative labels, and value-model training/scoring:

```powershell
.\.venv\Scripts\python.exe -m tactiq.evaluation --help
.\.venv\Scripts\python.exe -m tactiq.evaluation train-value --dataset training.json --version club-model-v1 --output .artifacts\model.json
.\.venv\Scripts\python.exe -m tactiq.evaluation score-value --dataset heldout.json --model .artifacts\model.json --output .artifacts\value-report.json
.\.venv\Scripts\python.exe -m tactiq.evaluation gate --report reviewed-readiness.json
```

Training and validation match IDs must be disjoint. Training does not approve a production model. Missing evidence fails closed.

| Claim | Evidence still needed |
| --- | --- |
| Football tracking quality | Licensed independently labelled players/ball, team assignment, identity and occlusion results |
| Physical geometry | Held-out survey landmarks, not points used to fit the transform |
| Tactical/value accuracy | Reviewed precision/recall and held-out xT/xG calibration |
| Grounded multilingual AI | Actual target-model outputs, failure traces, human review of enabled locales/voices |
| Replay quality | Measured synchronization/performance on representative devices and footage |
| Production readiness | Linux runtime, isolation, security review, restore/alerts, budget and staging load evidence |

[Quality CI](../../.github/workflows/quality.yml) runs regression/static/dependency/build checks and can enforce a reviewed readiness report. It is not a deployment workflow. Historical local check counts are not substituted for a current release report.
