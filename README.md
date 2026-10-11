# Match Intelligence (Hackathon Starter)

This is the first coding slice from the implementation plan:

- Seeded synthetic scenario replay
- Deterministic state + metrics
- Sustained-pressure detection with evidence
- FastAPI endpoints + WebSocket stream

## Run backend

1. Create and activate a Python 3.12 environment.
2. Install dependencies:
   - `pip install -e .[dev]`
   - For Foundry wiring: `pip install -e .[dev,foundry]`
3. Run API:
   - `uvicorn tactiq.presentation.api.main:app --app-dir services/backend --reload`

### Enable Foundry narrative generation

Set environment values (for example in `.env`) before starting the API:

- `FOUNDRY_ENABLED=true`
- `AZURE_FOUNDRY_PROJECT_ENDPOINT=<your_project_endpoint>`
- `AZURE_FOUNDRY_PROJECT_DEPLOYMENT_NAME=<your_model_deployment_name>`
- Optional key auth: `AZURE_FOUNDRY_API_KEY=<key>`

When Foundry is enabled and reachable, `/api/v1/matches/{match_id}/narratives` returns `"provider": "foundry"`. If Foundry fails, the API automatically falls back to deterministic grounded narrative output with `"provider": "deterministic"`.

### Enable Azure Speech synthesis (optional)

Set environment values:

- `AZURE_SPEECH_ENDPOINT=<your_speech_endpoint>`
- `AZURE_SPEECH_API_KEY=<your_speech_key>`

Then call:

- `POST /api/v1/speech/synthesize`
- `POST /api/v1/speech/synthesize-dual` (two-commentator mode)
- `POST /api/v1/matches/{match_id}/commentary/dual` (role-specialized dual script)

Optional voice overrides (two male commentator defaults):

- `AZURE_SPEECH_COMMENTATOR_A_VOICE`
- `AZURE_SPEECH_COMMENTATOR_B_VOICE`
- `AZURE_SPEECH_DEFAULT_VOICE`
- `AZURE_SPEECH_DEFAULT_VOICE_BY_LANGUAGE_JSON`
- `AZURE_SPEECH_DEFAULT_COMMENTATOR_A_VOICE`
- `AZURE_SPEECH_DEFAULT_COMMENTATOR_B_VOICE`
- `AZURE_SPEECH_DEFAULT_COMMENTATOR_PAIR_BY_LANGUAGE_JSON`

Optional Foundry commentary profile tuning:

- `FOUNDRY_COMMENTARY_PLAYBYPLAY_AGENT_NAME`
- `FOUNDRY_COMMENTARY_COLOR_AGENT_NAME`
- `FOUNDRY_COMMENTARY_PROMPT_TUNING_VERSION`
- `FOUNDRY_COMMENTARY_PLAYBYPLAY_PROFILE` (role guidance string)
- `FOUNDRY_COMMENTARY_COLOR_PROFILE` (role guidance string)

with:

```json
{
   "text": "Team A are building pressure.",
   "language": "en-GB"
}
```

When Azure Speech is unavailable, the UI falls back to browser speech synthesis.
The UI now supports live commentary plus optional dual-commentary mode.

> If you later add Microsoft Agent Framework package support, remember preview packages require prerelease flags (for Python: `pip install agent-framework-azure-ai --pre`).

## Run frontend

1. Go to [apps/web](apps/web).
2. Install dependencies:
   - `npm install`
3. Run:
   - `npm run dev`

Set `NEXT_PUBLIC_API_BASE_URL` for the frontend to reach the backend.

> Note: `npm install` reported 3 vulnerabilities in transitive packages from the default Next.js stack. For this first slice, functionality was prioritized; hardening is scheduled for later phases.

## First milestone flow

1. `POST /api/v1/matches` with:

   ```json
   { "scenario_id": "sustained_pressure", "seed": 42001 }
   ```

2. `POST /api/v1/matches/{match_id}/start`
3. Connect to `{NEXT_PUBLIC_API_BASE_URL with ws scheme}/api/v1/ws/matches/{match_id}`
4. Open `GET /api/v1/matches/{match_id}/insights` to verify evidence-backed insight.
5. Optional: call `GET /api/v1/scenarios` and run:
   - `sustained_pressure`
   - `rhythm_shift`
   - `player_influence`
6. Generate a grounded narrative:
   - `POST /api/v1/matches/{match_id}/narratives`
   - Example body:

     ```json
     {
       "audience_mode": "analyst",
       "language": "en-GB",
       "selected_player_id": "a_10"
     }
     ```

## Current scope

This currently includes deterministic insights, three core detectors, and a narrative endpoint with real Foundry wiring plus deterministic fallback behavior. Richer personalization, multilingual expansion, speech, and cloud hardening continue in later phases.

## Added in current iteration

- Post-match recap endpoint:
  - `POST /api/v1/matches/{match_id}/recap`
  - Uses accumulated insights and returns ranked top insights metadata.
- Localized speech path in UI:
  - The web app can speak the current narrative/recap using browser speech synthesis (`en-GB`, `sw-KE`, `fr-FR`).
- Broadcast overlay endpoint:
  - `GET /api/v1/matches/{match_id}/overlay`
  - Returns machine-readable timed payloads suitable for on-screen overlays.
- Viewer personalization profile endpoint:
  - `POST /api/v1/matches/{match_id}/viewer-profile`
  - Supports `favorite_team_id`, `favorite_player_id`, and `focus_metric`.
- Expanded real-time football metrics:
  - pass attempts/completions/accuracy, pass average distance, pass difficulty rating,
  - ball speed and shot speed,
  - player movement and high-speed action indicators.
- Video ingestion (auto-eventing + schema adapter):
   - `POST /api/v1/video/upload` (multipart video file)
      - Runs a lightweight motion-model auto-eventing pipeline (`video-auto-v1`) over frames.
      - Automatically falls back to deterministic stub extraction if runtime/video quality is insufficient.
   - `POST /api/v1/video/from-link` (URL string; deterministic stub extraction)
   - Both paths normalize events into the same `Event` schema and run the current intelligence pipeline.
