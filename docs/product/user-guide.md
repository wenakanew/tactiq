# The Tactiq experience

[Documentation](../README.md) · [Demo walkthrough](../submission/demo-guide.md)

## Choose a source

The public landing page introduces the product without starting analysis or calling paid providers. Enter `/workspace` to select a synthetic scenario or authorized video. `?start=demo` and `?start=video` select an entry flow; they do not automatically create work.

**Synthetic scenes** are the most reproducible demonstration. They contain generated events and 22 player trajectories, not proprietary league tracking.

**Video** accepts supported uploads and finite authorized links. YouTube links require rights confirmation; protected, live, login-required, or unavailable videos are not bypassed. Use an authorized original upload when a provider refuses access.

Video defaults to model-based CV. A missing detector is an error, not a silent switch to synthetic analysis. Analysis normally samples 8 FPS for up to 180 seconds; CV options permit up to 600 seconds. A completed segment is not necessarily a completed match.

## Explore without losing the moment

| Workspace area | Purpose |
| --- | --- |
| **The match** | Source, pitch, or split replay with a shared clock |
| **Moments** | Seek to a supported insight and inspect its evidence/clip |
| **The story** | Personalized explanations and recap |

The match stage remains mounted while changing sections. Seek, playback speed, and the selected moment share one source-time basis. Video scorelines remain unknown rather than displaying an invented 0–0.

Source-only tracking can be useful without camera calibration. Physical speed, formations, and surveyed pitch visualization require trustworthy geometry; directional tactics also require supplied attack directions. Marker heights are illustrative, not estimated human pose.

## Make it yours

Select Fan, Analyst, or Player, choose a language, and adjust explanation depth. Player mode does not attribute a team action to a player without supporting evidence. English, Kiswahili, French, Spanish, Brazilian Portuguese, Arabic, and Hindi have text-template fallbacks; human locale review is still required.

Loading an analysis does not start audio. **Listen** requests a manual explanation, pauses replay for deep commentary, and does not restart video when finished. Automatic commentary follows playback, skips stale work, and stops obsolete audio on pause/seek/session changes. Stop audio stops current playback, not just queued text.

Provider and fallback labels distinguish model output, deterministic text, cloud speech, browser voices, and unavailability. A locale appearing in the UI does not guarantee configured cloud voices.

## Access and saved work

Local mode is visibly unauthenticated and must stay private. The reference protected runtime uses workspace credentials and tenant-scoped roles, not Microsoft sign-in. Viewers inspect saved results; analysts create/control analysis; admins manage operations.

Only active match/job references and their tenant/subject association are remembered in tab-scoped session storage. Credentials, uploaded files, and narrative/media payloads are not stored there. Sign-out clears displayed private work. Leaving the page stops local playback/subscriptions, not an already submitted durable job.

The approved three-analysis guest allowance, Microsoft sign-in, and 24-hour guest retention are **planned/in progress**, not a promise of the current public experience. See [privacy](../operations/privacy-and-security.md).
