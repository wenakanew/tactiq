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

The frontend expects the backend at `http://localhost:8000` by default.

> Note: `npm install` reported 3 vulnerabilities in transitive packages from the default Next.js stack. For this first slice, functionality was prioritized; hardening is scheduled for later phases.

## First milestone flow

1. `POST /api/v1/matches` with:

   ```json
   { "scenario_id": "sustained_pressure", "seed": 42001 }
   ```

2. `POST /api/v1/matches/{match_id}/start`
3. Connect to `ws://localhost:8000/api/v1/ws/matches/{match_id}`
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
