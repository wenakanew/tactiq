# Tactiq UI + Data Visualization Modernization Plan

## Objective
Redesign the current web experience into a production-grade, broadcast-ready, fan-friendly football intelligence dashboard that clearly supports the pipeline:

Ingest → Interpret → Explain → Render → Personalize

Primary goals:
- Make the UI visually polished and modern.
- Make insights legible in real time.
- Make evidence and explainability first-class.
- Make personalization obvious and useful.
- Make the experience extensible for studio/broadcast and streaming use.

Current reference page: [apps/web/app/page.tsx](apps/web/app/page.tsx)

---

## 1) Product UX Vision

### 1.1 Core UX principles
- Evidence-first storytelling: every narrative should link to measurable signals.
- Live clarity over visual noise: fast scan, low cognitive load.
- Role-based depth: fan, analyst, player-focused views must feel genuinely different.
- Broadcast compatibility: overlay-ready data and timed cards.
- Trustworthy AI: provenance, confidence, and reason codes visible.

### 1.2 Target user modes
- Fan mode: short language, immediate match context, fewer metrics.
- Analyst mode: richer tactical metrics and trend charts.
- Player mode: selected player influence, burst events, impact timeline.
- Studio mode (future toggle): queue-ready cards and chapter summaries.

---

## 2) Information Architecture (New Page Layout)

## 2.1 Desktop layout (12-column grid)
- Top bar (full width)
  - Match status, clock, scoreline, scenario/source, language, mode controls.
- Left rail (3 cols)
  - Controls and personalization
  - Match setup
  - Viewer profile settings
  - Voice/language settings
- Main center (6 cols)
  - Live momentum + pressure visuals
  - Insight timeline stream
  - Commentary panel (dual commentator)
  - Narrative/recap tabs
- Right rail (3 cols)
  - Provenance card
  - Overlay priority queue
  - Alert panel (turning point, guardrails, quality notices)

## 2.2 Mobile layout
- Sticky compact match header
- Collapsible sections:
  1) Live
  2) Insights
  3) Commentary
  4) Analytics
  5) Recap
  6) Settings

## 2.3 Navigation model
- Single route is acceptable for hackathon; use internal tabbed sections:
  - Live Match
  - Insights
  - Commentary
  - Recap
  - Debug/Provenance

---

## 3) Visual Design System

### 3.1 Theming
- Base: dark-first professional sports UI.
- Accent colors:
  - Team A: blue family.
  - Team B: red/orange family.
  - Insight severity: low/medium/high mapped to neutral/amber/crimson.
- Confidence color ramp:
  - >0.85: strong green
  - 0.70–0.85: yellow
  - <0.70: muted orange/red

### 3.2 Typography
- Headline: bold condensed style for match moments.
- Body: high-legibility sans.
- Numeric metrics: tabular figures for stable scanning.

### 3.3 Component style language
- Rounded cards with subtle elevation.
- Thin grid lines for chart framing.
- Micro animations for updates (fade/slide, 150–250ms).
- Avoid heavy motion during active playback.

---

## 4) Data Visualization Blueprint

### 4.1 KPI strip (always visible)
- Possession split (bar + %)
- Progressive actions (team A vs B)
- Final-third entries
- Shots + shot speed max
- Pass accuracy + avg pass distance

### 4.2 Match momentum chart
- Time-series line chart with two team tracks.
- Overlay markers for:
  - turning_point
  - sustained_pressure
  - rhythm_shift
  - player_speed_burst

### 4.3 Pressure radar / phase wheel
- 6-axis radar for the active window:
  - intensity
  - progression
  - entries
  - shot threat
  - pass risk
  - transition speed

### 4.4 Insight timeline
- Vertical timeline cards ordered by time.
- Each card includes:
  - title
  - summary
  - confidence chip
  - reason code
  - quick evidence chips

### 4.5 Overlay priority queue panel
- Ranked top-N cards using backend score.
- Display:
  - queue rank
  - score
  - priority
  - freshness
  - render hints

### 4.6 Player influence arc (player mode)
- Sparkline trend for selected player contribution.
- Event markers for high-speed bursts and shot creation.
- Contribution ratio in current window.

### 4.7 Recap chapter visualization
- Chapter cards with mini timelines:
  - chapter title
  - window start/end
  - confidence
  - team context
  - provenance reason code

---

## 5) Explainability + Trust UX

### 5.1 Provenance drawer (right panel)
For selected insight/commentary show:
- reason code
- window (start/end)
- confidence
- evidence metric list
- supporting event IDs (collapsible)

### 5.2 Guardrail status indicator
- Badge states:
  - validated
  - caution (limited evidence)
  - blocked (guardrail fallback)

### 5.3 Commentary transparency
For each dual commentary pair:
- provider
- fallback_used
- orchestration_path
- agents used (name/version/source)

---

## 6) Commentary Experience Upgrade

### 6.1 Dual commentary stage
- Two-column “Lead” and “Analyst” cards.
- Per-line chips:
  - language
  - provider
  - confidence (if available)
- Anti-repetition indicator when line has been adjusted.

### 6.2 Speech controls
- Play/Pause/Replay last commentary
- Queue length indicator
- Voice profile preview chips
- Failure fallback messaging without technical jargon

---

## 7) Interaction and Motion

### 7.1 Live event microinteractions
- New event pulse on timeline
- Insight card enters with subtle slide
- Score changes animate numerically

### 7.2 State transitions
- Skeleton loaders for async fetches
- Empty states with actionable hints
- Error states with retry buttons

### 7.3 Accessibility
- WCAG contrast targets
- keyboard-first navigation
- ARIA labels for controls and charts
- reduced-motion support

---

## 8) Technical Frontend Architecture

### 8.1 Refactor structure
Split [apps/web/app/page.tsx](apps/web/app/page.tsx) into modular components:
- apps/web/components/layout/
- apps/web/components/match/
- apps/web/components/insights/
- apps/web/components/commentary/
- apps/web/components/recap/
- apps/web/components/provenance/
- apps/web/lib/

### 8.2 Suggested component map
- MatchHeader
- ControlPanel
- KPIBar
- MomentumChart
- PressureRadar
- InsightTimeline
- InsightCard
- ProvenancePanel
- OverlayQueuePanel
- CommentaryStage
- CommentaryAuditPanel
- RecapChaptersPanel
- PlayerInfluencePanel

### 8.3 Data layer
- Create typed API client wrappers in apps/web/lib/api.ts.
- Normalize API payloads into view models.
- Keep WS message handling isolated in a hook (useMatchStream).

### 8.4 State management
- Keep React state for now, but centralize with context + reducer for:
  - match state
  - events
  - insights
  - commentary queue/audit
  - recap chapters
  - personalization settings

---

## 9) Charting and UI Libraries

Recommended stack:
- UI: Tailwind CSS + shadcn/ui (rapid polished components)
- Charts: Recharts for speed, or ECharts for richer interaction
- Icons: Lucide
- Animations: Framer Motion (light usage)

If minimal-change path is preferred:
- Keep existing setup and add Recharts + lightweight CSS module tokens.

---

## 10) Backend Data Contracts to Surface on UI

Use existing endpoints and payload fields, especially:
- commentary provenance from [services/backend/tactiq/application/service.py](services/backend/tactiq/application/service.py)
- recap chapters from [services/backend/tactiq/presentation/api/main.py](services/backend/tactiq/presentation/api/main.py)
- websocket insight payloads with provenance
- overlay queue payload from /api/v1/matches/{match_id}/overlay

UI should explicitly render:
- reason_code
- confidence
- evidence metrics
- window bounds
- overlay_score / queue rank

---

## 11) Implementation Phases

## Phase 1: Foundation and visual overhaul (2–3 days)
- Build design tokens (colors, typography, spacing).
- Create shell layout + responsive grid.
- Move existing controls into left rail and top bar.
- Add KPI strip and improved status cards.

Deliverable:
- Modern baseline UI with better hierarchy and spacing.

## Phase 2: Core data visualizations (2–3 days)
- Implement MomentumChart and InsightTimeline.
- Add OverlayQueuePanel and ProvenancePanel.
- Add confidence/priority chips and reason code rendering.

Deliverable:
- Real-time visual storytelling and explainability visible.

## Phase 3: Commentary + recap enhancements (2 days)
- Build dual commentator stage.
- Add audit/provenance details and guardrail indicators.
- Add recap chapter cards with mini-timelines.

Deliverable:
- End-to-end explainable narration with chaptered recap UX.

## Phase 4: personalization depth (1–2 days)
- Build player influence arc.
- Add mode-specific card density and language style differences.
- Tune fan vs analyst vs player experiences.

Deliverable:
- Clearly differentiated audience experiences.

## Phase 5: polish + accessibility + performance (1–2 days)
- Keyboard and screen-reader pass.
- Reduced-motion support.
- Performance tuning (memoization, chart throttling).

Deliverable:
- Demo-ready, robust, polished product.

---

## 12) Acceptance Criteria

- UI feels “studio-grade,” not prototype-level.
- Insight provenance is visible without opening dev tools.
- Overlay queue is ranked and understandable.
- Recap chapters display with context and confidence.
- Commentary panel shows dual voices and audit metadata.
- Fan/Analyst/Player modes materially differ in depth.
- Mobile view remains usable and readable.

---

## 13) Demo Narrative (for judges)

1) Start synthetic live match.
2) Show live KPI strip and momentum chart shifting.
3) Highlight a turning point card and its provenance.
4) Play dual commentary and reveal audit metadata.
5) Open overlay queue to show render-ready ranking.
6) Switch audience mode (fan → analyst → player) to show depth changes.
7) End with chaptered recap and explainability trace.

---

## 14) Suggested Immediate Next Actions

1. Create a new UI refactor branch.
2. Break [apps/web/app/page.tsx](apps/web/app/page.tsx) into components first.
3. Implement Phase 1 layout + design tokens before adding more charts.
4. Add MomentumChart + InsightTimeline next (highest impact).
5. Add ProvenancePanel + OverlayQueuePanel immediately after to match project principle: AI explains, not invents.

---

## 15) Stretch Goals (if time permits)

- Tactical pitch map heat zones.
- Event replay scrubber tied to insight windows.
- Exportable studio package (JSON + overlay PNG snapshots).
- Multi-match comparison mode.
- “What changed?” delta explainer between two windows.

---

This plan is intentionally maximal. Implementation can be staged safely while preserving the current working backend contracts.