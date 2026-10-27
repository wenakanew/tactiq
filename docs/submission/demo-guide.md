# Demo and judge testing

[Documentation](../README.md) · [User guide](../product/user-guide.md) · [Rules and judging map](judging-guide.md) · [Submission description](project-description.md)

**Platform:** desktop browser, Next.js/React frontend and Python/FastAPI backend. No proprietary hardware is required. The challenge demonstration uses **project-generated, football-realistic synthetic events**, not real league footage.

**[Explore the live example](https://ca-tactiq-web-3e4d.salmongrass-337969b4.westus3.azurecontainerapps.io/example)** · [Watch the 1:55 YouTube pitch](https://youtu.be/ktO6Mbmkhy4) · [Private workspace](https://ca-tactiq-web-3e4d.salmongrass-337969b4.westus3.azurecontainerapps.io/workspace) · [Public source](https://github.com/wenakanew/tactiq)

YouTube player metadata on 2026-10-26 reported `Tactiq Pitch`, 115 seconds, playable and not private. The entrant must still check final signed-out playback and media rights, and put the video URL on the hackathon project page. A repository link is not a submitted form.

## Choose the correct testing route

| Route | Access and scope |
| --- | --- |
| **Live prepared public example** | Deployed October 27 at [`/example`](https://ca-tactiq-web-3e4d.salmongrass-337969b4.westus3.azurecontainerapps.io/example), no sign-in or backend API request. Real synthetic-engine replay, prepared deterministic explanations in seven locales, evidence, recap and matching offline English narration. No live model call. |
| **Free local synthetic test** | No Tactiq account, payment, provider key or cloud daily-admission cap. Demonstrates the analytical pipeline, replay, evidence, personalization and deterministic recap. Requires installing the open-source development prerequisites. |
| **Public Azure pilot** | The prepared [live example](https://ca-tactiq-web-3e4d.salmongrass-337969b4.westus3.azurecontainerapps.io/example) bypasses backend startup and new-analysis waits. Web/API now retain one small serving replica each. Private new analysis still has up to fifteen-minute initial dispatch, one new native analysis per UTC day globally, ownership, guest expiry and a fixed shared paid-generation budget. Restarts and deployments can still cause delays. Do not describe this route as unrestricted live AI access. |
| **Verified Azure AI/media receipts** | Published Foundry/Framework/Speech and private-storage evidence proves those particular checks. It is not a substitute for live interactive access or a promise of unlimited paid generation. |

Rule 4.8 requires free testing availability through November 10, 2026 at 11:59 pm Pacific. The entrant must maintain the offered hosted example and local test route, and resolve any need for unrestricted live paid-provider testing before asserting complete access compliance. No analysis, ownership or paid-generation controls were disabled to provide these routes.

## Immediate prepared example

**For judges:** open the [live prepared example](https://ca-tactiq-web-3e4d.salmongrass-337969b4.westus3.azurecontainerapps.io/example) directly. No setup is required.

**For local reproduction:** run this revision's frontend with Node 22.13+; Python, Azure credentials, a database and FFmpeg are not needed to **browse the checked-in example**. From the repository root:

```powershell
Set-Location apps\web
npm ci
node node_modules\next\dist\bin\next dev --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000/example`, or choose **Explore an example match** from the landing page. **Start a new analysis** still opens the original pipeline and its controls.

1. Select **Pressure Building**, play/pause the real replay, seek, or switch between 2D and the simulated 2.5D scene.
2. Compare **Fan** with **Analyst**, then change the locale. The analyst explanation leads with supporting measurements.
3. Expand **Explore the supporting evidence** and **How was this made?** to see the selected window, events, metrics, recipe, seed and output source.
4. With **Pressure Building**, **English** and **Fan** selected, play the matching prepared offline narration. Other combinations disclose unavailable audio rather than substituting a mismatched recording.
5. Read the recap and use its moment buttons. Separately, the labelled Azure exchange below is an existing provider recording about a different passage, with its own transcript and provenance.

Replay playback and audio are independent; narration pauses replay, not synchronized word-by-word commentary. The example is illustrative synthetic football, not a real-match benchmark. It does not run Foundry or the lead/analyst workflow on opening. [Original browser capture](../assets/examples/public-example.png) and [verification report](../development/judge-experience.md) show the local checks. The example and current backend fixes were subsequently [deployed and verified on Azure on October 27](../azure/deployment.md#judge-experience-refresh-october-27).

## Free local judge testing

Use Python 3.12+ (3.13 recommended), Node 22.13+, Git, and FFmpeg/FFprobe. Follow [full development setup](../development/getting-started.md) for prerequisites. The commands below use PowerShell on a desktop computer. No `.env` secrets, Azure account or uploaded football footage are required.

From a fresh clone:

```powershell
git clone https://github.com/wenakanew/tactiq.git
Set-Location tactiq
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

$env:APP_ENV="local"
$env:TACTIQ_RUNTIME_MODE="local"
$env:TACTIQ_AUTH_ENABLED="false"
$env:FOUNDRY_ENABLED="false"
$env:TACTIQ_CV_DETECTOR="motion"
$env:AZURE_FOUNDRY_API_KEY=""
$env:AZURE_SPEECH_API_KEY=""
$env:AZURE_SPEECH_KEY=""
$env:TACTIQ_CORS_ALLOW_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
$env:TACTIQ_DATABASE_PATH=".artifacts\judge-local.sqlite3"
$env:TACTIQ_MEDIA_DIR=".artifacts\judge-local-media"
.\.venv\Scripts\python.exe -m uvicorn tactiq.presentation.api.main:app --app-dir services\backend --host 127.0.0.1 --port 8765
```

Do not add `--env-file .env` for this credential-free test. Keep this unauthenticated local service on loopback. The motion detector setting is a development option, not a claim of football-video detection quality; the synthetic scenario needs no ONNX weights.

In a second terminal, from the same clone:

```powershell
Set-Location apps\web
npm ci
$env:TACTIQ_BACKEND_URL="http://127.0.0.1:8765"
$env:NEXT_PUBLIC_API_BASE_URL=""
$env:NEXT_PUBLIC_ENABLE_CLOUD_SPEECH="false"
node node_modules\next\dist\bin\next dev --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000/workspace`. A fresh clone avoids stale `.env.local`, production rewrites and shared test data. If using an existing checkout, do not overwrite its private configuration; use separate data paths and unused ports, adjusting origins consistently. Ctrl+C stops each service.

### Reproducible five-stage walkthrough

1. **Ingest:** select **Try a demo**, then **Building the pressure**. Turn off **Live commentary** and **Dual commentary** before **Start live scenario**. This uses the synthetic engine without paid generation. Wait for **Full time**; preparation time depends on the machine and is not a live-broadcast benchmark.
2. **Interpret:** inspect the resulting supported moments and match statistics. The catalogue also includes rhythm-shift and player-influence scenarios. Seed/recipe defaults are documented in the [challenge map](judging-guide.md#challenge-one-pipeline-five-stages).
3. **Explain:** select **Pressure Building** on the replay's insight controls. Read the selected passage, expand its breakdown and inspect its supporting measurements. The local explanation is labelled deterministic/fallback, not Foundry output.
4. **Render:** press **Play replay**, pause, seek, and compare the time cursor with the pitch view. Generated player/ball positions are synthetic, not camera observations. Explore **Moments** and the JSON/CSV exports.
5. **Personalize:** switch **Fan** to **Analyst**, then try **Player** only where attribution is supported. Change **Language** to Swahili and back to English, checking that the evidence remains consistent. Under **The story**, press **Generate recap** and inspect its chapters. Local recap generation uses deterministic text.

The intended result is a completed synthetic match with inspectable moments, synchronized replay and a recap. The published cloud demonstration produced six insights; do not treat that count as a requirement for every scenario/version/input. Browser speech is optional and depends on installed voices; it is not necessary to complete the test or evidence of Azure Speech.

## Azure provider and deployment evidence

The [release record](../azure/deployment.md) separates public-browser checks from management-plane provider checks. [Single narrative](../assets/production/foundry-commentary.json) and [ordered lead/analyst trace](../assets/production/dual-commentary.json) record genuine no-fallback model results. The [MP3](../assets/production/commentary-live.mp3) is verified Azure dual-voice output from existing synthetic evidence, not proof that the local demo made a model call.

Do not supply private credentials in public instructions, reuse another guest's cookie, move match ownership, reset cloud caps or release ambiguous budget reservations for a demo. A fresh guest cannot inspect another workspace's match. Microsoft login redirect is verified; interactive sign-in remains unverified.

## Submission video and footage

The video must remain **under 120 seconds** and show actual functioning software on its intended platform. Describe local/reference footage as local; distinguish separately verified Azure audio from in-browser playback. The local pitch was rendered at approximately 115 seconds with English narration/captions, genuine application capture, labelled synthetic data and no background music. The linked upload's reported duration matches, but byte identity and complete rights clearance have not been established.

The synthetic path avoids needing external match footage. If showing an optional video input, obtain analysis, playback, publication and submission rights first. An accessible URL is not permission, and organizers do not supply league footage. Unknown score, missing ball and uncalibrated geometry must remain visible. Review [third-party notices](../reference/third-party-notices.md) and the [rights checklist](judging-guide.md#rights-and-submission-media-review), including voice and branding permissions.

## Presenter checklist

- Make the five required stages visible and lead with the viewer's problem, not service logos.
- Keep source labels and fallback/provider status readable. Do not turn synthetic speed fields into real-video accuracy claims.
- Explain how the lead/analyst roles share evidence and validate a handoff; do not claim a single-agent comparison that was never measured.
- Provide English text, narration, captions and testing instructions; translate any non-English demonstration segment.
- Confirm the public code/video links, final project-page fields, timely registration, entrant eligibility, ownership and continued free testing access.
- Keep cold starts, guest expiry, budgets and processing limits explicit; do not promise HA, unrestricted hosted processing or a certified enterprise release.
