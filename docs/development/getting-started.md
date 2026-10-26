# Local development

[Documentation](../README.md) · [Testing](testing.md) · [Configuration](../reference/configuration.md)

## Prerequisites

Use Python 3.12+ (3.13 recommended), Node 22.13+, and FFmpeg/FFprobe with H.264/AAC support. The locked backend includes the YouTube resolver/EJS dependencies; Node is needed on the backend host too.

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Never overwrite an existing `.env` or private `apps\web\.env.local`. For a credential-free synthetic development session, explicitly disable providers and select isolated data paths before starting:

```powershell
$env:APP_ENV="local"
$env:FOUNDRY_ENABLED="false"
$env:TACTIQ_AUTH_ENABLED="false"
$env:TACTIQ_MEDIA_DIR=".artifacts\local-media"
$env:TACTIQ_DATABASE_PATH=".artifacts\local.sqlite3"
.\.venv\Scripts\python.exe -m uvicorn tactiq.presentation.api.main:app --app-dir services\backend --env-file .env --host 127.0.0.1 --port 8765
```

Environment variables override the environment-file values. Local mode is unauthenticated: bind to loopback, not a public interface. Use **one worker** for this reference runtime. Choose another unused port if a preview is already running; do not stop or reuse its database/media.

In a second PowerShell terminal:

```powershell
Set-Location apps\web
npm ci
$env:TACTIQ_BACKEND_URL="http://127.0.0.1:8765"
$env:NEXT_PUBLIC_ENABLE_CLOUD_SPEECH="false"
npm run dev
```

Open `http://localhost:3000` or `/workspace`. The backend exposes `/health/live`, `/health/ready`, and `/docs`. `TACTIQ_BACKEND_URL` enables the same-origin HTTP/media/WebSocket proxy. Use `NEXT_PUBLIC_API_BASE_URL` only for an intentional direct-origin deployment. Restart/rebuild Next after changing these settings.

## Optional real video

Supply a separately licensed ONNX artifact outside Git. The application does not download weights. Configure the detector path/format/classes as described in [configuration](../reference/configuration.md#video-and-cv); a missing detector makes CV fail explicitly. Use synthetic scenarios when no model is available.

For physical pitch views, supply measured image/field correspondences for the actual footage, with fresh keyframes after cuts. Pitch coordinates are percentages of a 105 × 68 metre field; arbitrary sample coordinates are not a valid camera survey.

## Packaging

[Backend Dockerfile](../../Dockerfile), [web Dockerfile](../../apps/web/Dockerfile), and [Compose](../../compose.yml) package the single-host reference, not a completed cloud migration. Compose defaults to local mode. Review private volumes, secrets, origin/auth settings, and the [runbook](../operations/runbook.md) before use beyond loopback.

The current Windows environment cannot validate Docker/Podman images. Linux image/runtime verification remains a release gate, not an inferred success.
