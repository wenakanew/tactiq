# Match Intelligence Project — Hackathon Implementation Blueprint

> **Status:** Lean hackathon execution baseline  
> **Version:** 1.1  
> **Date:** 2026-10-01  
> **Submission deadline:** 2026-10-27  
> **Source specification:** [project.md](project.md)  
> **Working identifier:** `match-intelligence` — the public product name remains undecided  
> **Purpose:** Deliver one undeniable end-to-end match-intelligence experience first, then expand only while the core remains demo-ready.

---

## 1. Executive implementation brief

This project is a real-time, agentic football intelligence application. It runs a reproducible synthetic match, converts its event stream into deterministic football metrics, detects meaningful patterns, attaches verifiable evidence, and uses Microsoft Foundry agents to turn those facts into personalized narratives. The web experience renders the match, explains each insight, supports player-focused analysis, can speak localized commentary, and generates a grounded post-match recap.

This document preserves the production-worthy architecture but changes the execution strategy for a 27-day hackathon window:

> **Build one undeniable vertical slice, keep it deployable, and add capabilities in descending order of judging value.**

The implementation must preserve one invariant:

> **Events produce facts; facts produce patterns; agents explain patterns. Agents never manufacture match facts.**

### 1.1 MVP outcome

A judge must be able to:

1. Start a seeded synthetic match scenario.
2. Watch events, score, clock, players, and the ball update live.
3. See an evidence-backed insight appear after a known pattern emerges.
4. Switch between casual, analyst, and player-focused modes.
5. Select a player and receive only evidence-supported player commentary.
6. Switch between English and Kiswahili without changing the underlying facts; add French next and the remaining languages only after the core is stable.
7. Play spoken commentary for the demonstrated language path, with broader voice coverage added by tier.
8. Open the evidence panel and inspect the source window and metrics.
9. Reach full time and receive a personalized, multilingual post-match recap.
10. Continue using deterministic insights if an AI, translation, or speech dependency fails.

### 1.2 Submission-critical MVP capabilities

| Capability | MVP status | Proof in demo |
| --- | ---: | --- |
| Seeded synthetic match simulator | Required | Same seed reproduces the same match |
| Live event stream | Required | Timeline and pitch update continuously |
| Deterministic football metrics | Required | Evidence panel shows calculated values |
| Three polished pattern families | Required | Sustained pressure, momentum/rhythm shift, and player influence trigger reliably |
| Explainable insights | Required | Every claim links to events and a time window |
| Purposeful multi-agent workflow | Required | Trace shows interpretation and narrative stages |
| Casual and analyst modes | Required | Same insight changes detail, not facts |
| Player mode | **Required** | Selected player's supported contribution is emphasized |
| English and Kiswahili text | Required | Language switch preserves facts and evidence |
| Demonstrated localized speech | **Required** | The showcased localized insight can be played as audio |
| Post-match recap | **Required** | Full-time view ranks and summarizes stored insights |
| Web match visualization | Required | Synthetic pitch, score, clock, events, insights |
| Broadcast-compatible JSON | Required | Structured output is inspectable/downloadable |
| Minimal Azure deployment and telemetry | Required | Hosted vertical slice, Foundry call, and correlated trace evidence |

The following are **showcase targets, not blockers for the submission-critical build**:

- French text and speech after English and Kiswahili are stable.
- Spanish, Brazilian Portuguese, Arabic, and Hindi text and speech.
- Tactical-shift and counterattack detectors beyond the three core patterns.
- Event Hubs, Cosmos DB, Blob Storage, complete Bicep, and advanced dashboards when simpler adapters already support the demo.
- Full production resilience, scale, and security hardening beyond the hackathon threat model.

### 1.3 Explicitly out of MVP

- Processing copyrighted match video or proprietary live data.
- Computer-vision auto-eventing.
- User-authored conversational questions.
- Voice input.
- Betting, outcome prediction, or player valuation.
- Multiple concurrent matches in the product UI.
- Production-grade broadcaster integration.
- Permanent user accounts or cross-device preference sync.
- Automatic video highlight generation.

These exclusions protect the core two-minute experience. The internal design may remain extensible without implementing them.

### 1.4 Delivery tiers and scope-freeze rules

| Tier | Scope | Release rule |
| --- | --- | --- |
| Tier 0 — Undeniable core | Simulator → local stream → state → sustained pressure → evidence → browser | Nothing else starts until this is green |
| Tier 1 — Judging MVP | Three patterns, Foundry narrative, fan/analyst/player, English/Kiswahili text, one Kiswahili speech path, recap, minimal Azure deployment | Required for submission |
| Tier 2 — Wow factor | French text/speech, polished transitions, architecture/trace view | Add only while Tier 1 remains green |
| Tier 3 — Breadth | Remaining four languages/voices, two extra patterns, broadcast export | Time-permitting showcase |
| Tier 4 — Production path | Event Hubs, Cosmos DB, Blob, complete IaC/CI, load/resilience hardening | Implement selectively; document the rest |

Scope rules:

1. A lower tier may not be delayed to complete a higher tier.
2. Every merge must leave the canonical demo path runnable.
3. Freeze new features on October 20; October 21–27 is for integration, evaluation, demo recording, documentation, and contingency.
4. If a Tier 1 capability is unstable by October 20, remove optional breadth before weakening grounding or the deterministic core.
5. Seven-language support remains the architectural target, but only validated locales may appear as enabled in the UI.

### 1.5 MVP quality targets

| Measure | Target |
| --- | ---: |
| Event-to-state processing, p95 | ≤ 100 ms |
| Pattern detection after qualifying event, p95 | ≤ 250 ms |
| Deterministic insight visible, p95 | ≤ 500 ms |
| AI-enhanced narrative visible, p95 | ≤ 4 s |
| Speech begins after request, p95 | ≤ 3 s when uncached |
| Stream reconnection | ≤ 5 s |
| Grounding violations in release evaluation | 0 critical; < 1% minor |
| Seed replay equality | 100% event equality |
| MVP pattern detection on golden scenarios | 100% |
| Supported-language entity/number preservation | 100% |
| Accessibility target | WCAG 2.2 AA for the MVP journey |

---

## 2. Implementation principles

1. **Deterministic core:** simulation, metrics, pattern detection, evidence, confidence, and priority are ordinary code.
2. **Semantic boundary:** agents receive a versioned semantic insight, never an unbounded raw match dump.
3. **Structured generation:** every agent response must satisfy a schema before it can reach the UI.
4. **Evidence inheritance:** generated representations retain the same evidence IDs and cannot add metrics.
5. **Language independence:** football meaning is finalized before localization.
6. **Text before audio:** validated localized text is the only input to text-to-speech.
7. **Graceful degradation:** each cloud-dependent stage has a deterministic or cached fallback.
8. **Seeded reproducibility:** demo and regression scenarios are deterministic.
9. **One live vertical slice first:** build scenario → stream → metrics → insight → UI before expanding breadth.
10. **No hidden intelligence:** confidence, window, evidence, prompt version, model deployment, and fallbacks are traceable.

---

## 3. Chosen architecture

### 3.1 Technology decisions

| Area | Decision | Reason |
| --- | --- | --- |
| Web | Next.js + TypeScript + Tailwind CSS | Fast polished UI, typed contracts, responsive rendering |
| Pitch | SVG rendered in React | Accessible, lightweight, deterministic, easy to animate |
| API | Python 3.12 + FastAPI + Pydantic | Fits simulation/analytics and produces OpenAPI contracts |
| Real-time browser channel | WebSocket | Bidirectional lifecycle controls and low-latency state updates |
| Internal local event bus | Bounded `asyncio.Queue` | Simple, testable, no cloud dependency for local execution |
| Event transport | Bounded `asyncio.Queue` first; Azure Event Hubs adapter as Tier 4 | Keeps the demo reliable while preserving the cloud-scale path |
| Agent SDK | Microsoft Agent Framework for Python | Structured, observable Foundry agent workflows |
| Foundry client | `AIProjectClient` | Current Foundry agent integration and managed agent capability |
| Localization | Azure AI Translator + reviewed football glossary | Tiered multilingual delivery without re-reasoning |
| Speech | Azure Speech text-to-speech | Locale-aware synthesis, SSML controls, cacheable audio |
| Persistence | In-memory/file-backed match repository first; Azure Cosmos DB for NoSQL as Tier 4 | Avoids blocking the slice while preserving document-oriented replay design |
| Audio/exports | Browser/in-process cache first; Azure Blob Storage when cloud artifacts are needed | Speech and recap work before storage infrastructure is complete |
| Hosting | Azure Container Apps | Separate API, simulator worker, intelligence worker, and web containers |
| Secrets | Azure Key Vault + managed identity | No credentials in code or environment files in Azure |
| Telemetry | Azure Monitor + Application Insights + OpenTelemetry | Correlated event-to-agent-to-UI traces |
| Infrastructure | Bicep | Repeatable Azure environments and teardown |
| CI/CD | GitHub Actions | Public-repository validation and deployment workflow |

The local profile runs the queue and service adapters in-process. The Azure profile substitutes Event Hubs, Cosmos DB, Blob Storage, Translator, Speech, and Foundry through interfaces. Domain code must not import Azure SDKs directly.

### 3.2 Logical architecture

```text
Scenario definition
       │
       ▼
Synthetic simulator ──► Event publisher ──► Event Hubs / local queue
                                               │
                                               ▼
                                      Intelligence engine
                                      state + rolling metrics
                                               │
                                               ▼
                                        Pattern detector
                                     evidence + confidence
                                               │
                         ┌─────────────────────┴────────────────────┐
                         ▼                                          ▼
                deterministic copy                         Agent workflow
                         │                         tactical → narrative → persona
                         │                                          │
                         └─────────────────────┬────────────────────┘
                                               ▼
                                   Localization + glossary checks
                                               │
                                 ┌─────────────┴─────────────┐
                                 ▼                           ▼
                          WebSocket/UI                 Speech synthesis
                                 │                           │
                                 └─────────────┬─────────────┘
                                               ▼
                                     Persist significant insights
                                               │
                                               ▼
                                      Full-time recap workflow
```

### 3.3 Deployable components

| Component | Responsibility | Scaling rule |
| --- | --- | --- |
| `web` | UI, static assets, client state | HTTP concurrency |
| `api` | REST, WebSocket, orchestration facade | HTTP/WebSocket concurrency; min replica 1 during demo |
| `simulator-worker` | Seeded scenario execution and event publication | One active job per match |
| `intelligence-worker` | Consume events, update state, detect patterns | Event Hub lag |
| `agent-worker` | Foundry workflow, localization, speech requests, recap | Queue depth; strict concurrency cap |

For the first vertical slice, `api`, simulator, intelligence, and agents may run in one Python process behind interfaces. Split deployment occurs only after all contracts and integration tests pass.

### 3.4 Allowed dependency direction

```text
presentation → application → domain
infrastructure ────────────────┘
```

- `domain` contains no FastAPI, Foundry, Azure, or database imports.
- `application` coordinates domain services through ports.
- `infrastructure` implements event, persistence, AI, translation, and speech ports.
- `presentation` owns HTTP/WebSocket and browser mapping.
- Agents cannot write match state or calculated metrics.

### 3.5 Early Azure and Foundry risk spike

Azure must not wait until the platform is otherwise complete. During the first week, create a disposable integration spike that proves:

1. The backend can authenticate to a Microsoft Foundry project through `AIProjectClient`.
2. One hard-coded, schema-valid semantic insight can produce a structured narrative.
3. The smallest web/API vertical slice can run in Azure Container Apps.
4. Application Insights receives one correlated request/agent trace.

This spike is not the production architecture and must not block Tier 0. Its purpose is to expose identity, quota, region, SDK, deployment, and networking problems while there is still recovery time. Delete or replace spike code after the real adapters are integrated.

Event Hubs, Cosmos DB, Blob Storage, Key Vault, and complete Bicep remain target architecture. They are introduced only when they solve a demonstrated MVP need. The submission may use an in-process queue and bounded match-local persistence if those choices produce a more reliable judged experience; the architecture diagram must distinguish **implemented now** from **production path**.

---

## 4. Repository structure

```text
project/
├── README.md
├── project.md
├── implementation.md
├── LICENSE
├── .env.example
├── .gitignore
├── pyproject.toml
├── package.json
├── apps/
│   └── web/
│       ├── app/
│       ├── components/
│       ├── features/
│       ├── lib/
│       └── tests/
├── services/
│   └── backend/
│       └── match_intelligence/
│           ├── domain/
│           │   ├── events/
│           │   ├── match/
│           │   ├── metrics/
│           │   ├── patterns/
│           │   └── insights/
│           ├── application/
│           │   ├── commands/
│           │   ├── queries/
│           │   ├── ports/
│           │   └── workflows/
│           ├── infrastructure/
│           │   ├── agents/
│           │   ├── azure/
│           │   ├── localization/
│           │   ├── persistence/
│           │   └── speech/
│           └── presentation/
│               ├── api/
│               └── websocket/
├── contracts/
│   ├── json-schema/
│   ├── openapi/
│   └── generated/
├── data/
│   ├── scenarios/
│   ├── teams/
│   ├── glossaries/
│   └── golden/
├── evals/
│   ├── grounding/
│   ├── personalization/
│   ├── localization/
│   └── recap/
├── tests/
│   ├── unit/
│   ├── property/
│   ├── contract/
│   ├── integration/
│   ├── e2e/
│   └── performance/
├── infrastructure/
│   ├── bicep/
│   └── environments/
├── scripts/
└── docs/
    ├── adr/
    ├── diagrams/
    ├── runbooks/
    └── demo-script.md
```

Use a Python workspace and a JavaScript workspace, but keep one lock file per ecosystem. Generate TypeScript types from the canonical JSON schemas; do not maintain duplicate handwritten contracts.

---

## 5. Canonical domain contracts

All messages use JSON, UTC ISO 8601 wall-clock timestamps, integer match time, and `schema_version`. Identifiers are UUIDv7 where generated at runtime. Scenario fixture IDs may be stable readable strings.

### 5.1 Shared conventions

- `match_clock_ms`: elapsed active-match milliseconds; authoritative for football windows.
- `occurred_at`: UTC simulation timestamp; used for telemetry and storage.
- Pitch coordinates: $x \in [0,100]$ from the possessing team's own goal toward the opponent goal; $y \in [0,100]$ from left touchline to right touchline.
- The simulator normalizes direction before publishing events.
- Distances use metres; speeds use metres per second; ratios use $[0,1]$.
- Every event has `event_id`, `match_id`, `sequence`, and `schema_version`.
- `sequence` is strictly increasing within a match and is the ordering authority.
- Consumers are idempotent on `(match_id, event_id)`.
- Every record contains `synthetic: true`.

### 5.2 Event envelope

```json
{
  "schema_version": "1.0.0",
  "event_id": "0199...",
  "match_id": "match_demo_001",
  "sequence": 184,
  "occurred_at": "2026-10-01T14:07:42.000Z",
  "match_clock_ms": 4062000,
  "period": 2,
  "type": "pass_completed",
  "team_id": "team_a",
  "player_id": "a_08",
  "target_player_id": "a_11",
  "position": { "x": 54.2, "y": 42.1 },
  "end_position": { "x": 71.6, "y": 36.4 },
  "qualifiers": {
    "distance_m": 18.4,
    "progressive": true,
    "channel": "left_half_space"
  },
  "scenario_id": "sustained_pressure",
  "seed": 42001,
  "synthetic": true
}
```

MVP event types:

- Match: `match_started`, `period_started`, `period_ended`, `match_ended`.
- Ball: `pass_attempted`, `pass_completed`, `pass_failed`, `carry`, `shot`, `goal`.
- Possession: `recovery`, `turnover`, `interception`, `tackle`, `possession_started`.
- Tactical: `pressure`, `substitution`, `formation_changed`.
- Discipline: `foul`.

Unknown event types are persisted and ignored by metrics until supported. Invalid events are dead-lettered with a reason and never sent to agents.

### 5.3 Match state

`MatchState` contains:

- Match identity, period, status, clock, score, and last processed sequence.
- Team formations and active players.
- Current possession team and possession start time.
- Latest normalized player and ball positions.
- Cumulative team/player counters.
- Rolling metric snapshots for 30, 60, 90, 300 seconds, half, and match.
- Active insight IDs and last-emitted time by pattern type.
- State revision incremented after every accepted event.

State is reconstructed by ordered replay. Snapshots may accelerate recovery but the event log remains the source of truth.

### 5.4 Metric snapshot

```json
{
  "schema_version": "1.0.0",
  "match_id": "match_demo_001",
  "as_of_sequence": 184,
  "as_of_match_clock_ms": 4062000,
  "window_ms": 90000,
  "team_id": "team_a",
  "metrics": {
    "possession_share": 0.67,
    "progressive_actions": 6,
    "final_third_entries": 4,
    "shots": 1,
    "territory_index": 0.71,
    "event_intensity_per_min": 12.7
  },
  "synthetic": true
}
```

### 5.5 Semantic insight

A semantic insight is the immutable handoff between deterministic and generative layers.

Required fields:

- `insight_id`, `match_id`, `schema_version`, `type`, `status`.
- `team_id` and optional `player_ids`.
- `window.start_ms`, `window.end_ms`.
- `trigger_event_ids` and `evidence_items`.
- `confidence` and `confidence_components`.
- `priority` and `priority_score`.
- `concept`: language-neutral subject, relation, object, qualifiers, and temporal scope.
- `deterministic_title` and `deterministic_summary` in English.
- `provenance`: detector version, metric version, scenario, seed, state revision.
- `generated_representations`: references only; never embedded into the immutable facts.

Each `EvidenceItem` includes a metric name, typed value, unit, window, team/player scope, and supporting event IDs.

### 5.6 Narrative contract

```json
{
  "narrative_id": "nar_...",
  "insight_id": "ins_...",
  "audience_mode": "analyst",
  "language": "en-GB",
  "title": "Pressure building",
  "body": "Team A have increased territorial control...",
  "speech_text": "Team A have increased territorial control...",
  "used_evidence_ids": ["evd_1", "evd_2"],
  "claims": [
    { "text": "four final-third entries", "evidence_id": "evd_2" }
  ],
  "generation": {
    "workflow_version": "1.0.0",
    "prompt_version": "1.0.0",
    "model_deployment": "configured-at-deploy",
    "fallback_used": false
  }
}
```

A narrative is rejected if a numerical claim has no exact evidence match, an entity is unknown, its temporal scope exceeds the insight window, or schema validation fails.

### 5.7 User preferences

```json
{
  "audience_mode": "player",
  "selected_team_id": "team_a",
  "selected_player_id": "a_08",
  "preferred_metric": "progression",
  "language": "sw-KE",
  "output": ["text", "speech"],
  "verbosity": "concise",
  "auto_play_speech": false
}
```

Preferences are session-scoped in MVP and persisted in browser storage. The API validates selected team/player membership.

### 5.8 Recap contract

A recap contains:

- Final score and match metadata.
- Ranked insight references with evidence retained.
- First-half, second-half, and turning-point sections.
- Team summaries.
- Optional selected-player summary.
- Audience mode and language.
- A list of claims mapped to insight/evidence IDs.
- Generation provenance and fallback status.
- Text, speech-text, and optional audio artifact URI.

---

## 6. Synthetic simulator

### 6.1 Runtime model

The simulator is a seeded state machine, not unconstrained random JSON generation.

State includes match clock, period, score, possession, phase of play, ball zone, active players, formation, fatigue proxy, and scenario cursor. The seed controls all probabilistic choices. Given identical scenario version, seed, and simulator version, emitted events must be byte-equivalent except `occurred_at`.

Controls:

- Start, pause, resume, reset, and stop.
- Speed: `0.5x`, `1x`, `2x`, `5x`, and deterministic instant replay for tests.
- Scenario and seed selection.
- Optional network delay, duplicate, malformed event, and stream interruption injection.

### 6.2 Football invariants

Property tests enforce:

- Exactly one team has possession when the ball is in play.
- Active player belongs to the referenced team.
- A substituted-out player cannot act later.
- Clock and event sequence never move backward.
- Coordinates remain within pitch bounds.
- A completed pass targets a teammate.
- A goal increments exactly one score once.
- Formation contains eleven active players before substitutions affect it.
- Match ends only after the final period.
- Every event is marked synthetic.

### 6.3 Prioritized scenario fixtures

| Priority | Scenario | Scripted signal | Expected pattern |
| --- | --- | --- | --- |
| Core 1 | Sustained pressure | ≥ 5 progressive actions, ≥ 3 final-third entries, ≥ 60% possession, shot within 90 s | `sustained_pressure` |
| Core 2 | Momentum/rhythm shift | Territorial control changes team, current intensity rises, and the previously quieter team produces repeated attacks | `rhythm_change` |
| Core 3 | Player influence | Selected player involved in ≥ 60% of team's recent progressive actions and creates a shot | `player_influence` |
| Stretch 1 | Counterattack | Recovery to shot ≤ 12 s, ≥ 2 progressive actions, ≥ 35 m advancement | `counterattack` |
| Stretch 2 | Tactical shift | Substitution/formation change followed by channel share increase ≥ 20 percentage points over 5 min | `tactical_shift` |

The three core fixtures define preconditions, ordered key events, acceptable filler events, expected detection window, expected evidence values, forbidden insights, and golden replay output. Stretch fixtures are added only after all Tier 1 acceptance tests pass. The UI calls `rhythm_change` **Momentum Shift** for accessible storytelling while the evidence panel exposes the exact rhythm and territory metrics; it must not imply a predictive or mystical momentum score.

### 6.4 Backpressure

Publishers use a bounded buffer. In local mode the simulator slows when the queue reaches 80% capacity. In cloud mode Event Hubs retains the stream and the UI receives a `feed_delayed` status when consumer lag exceeds two simulated seconds. Events are never silently dropped.

---

## 7. Football intelligence engine

### 7.1 Rolling windows

Maintain deques indexed by match time for 30 s, 60 s, 90 s, and 5 min windows. Cumulative half/match counters are separate. Late events are accepted only if they are within a configurable 2-second tolerance and do not precede the last committed snapshot. Later events enter the dead-letter flow for MVP; replay mode can rebuild state in order.

### 7.2 Metric definitions

Let $W$ be the active time window and $E_W$ its events.

#### Possession share

$$
P_t(W)=\frac{\text{controlled possession milliseconds for team }t}{\text{controlled possession milliseconds for both teams}}
$$

Dead-ball time is excluded. If the denominator is zero, the value is `null`, not zero.

#### Progressive action

A completed pass or carry is progressive when it advances the ball toward the opponent goal by at least:

- 10 pitch-percentage points from the defensive 60%; or
- 5 points when starting in the attacking 40%;

and does not end farther from goal. Thresholds are configuration with versioned defaults.

#### Final-third entry

A completed pass or carry crossing from $x < 66.67$ to $x \ge 66.67$. Consecutive actions in one possession count separately for evidence but a possession-level entry count also prevents inflated UI summaries.

#### Territory index

$$
T_t(W)=\frac{1}{N}\sum_{i=1}^{N}\frac{x_i}{100}
$$

where $x_i$ is the normalized ball position during team $t$'s controlled on-ball samples. Return `null` for $N=0$.

#### Event intensity

$$
I(W)=\frac{|E_W^{meaningful}|}{|W|\text{ in minutes}}
$$

Meaningful events exclude clock/status heartbeats.

#### Player involvement

$$
V_p(W)=\frac{\text{qualifying on-ball or defensive actions by }p}{\text{qualifying team actions}}
$$

Expose the numerator and denominator; never call a low-denominator ratio influential.

#### Transition speed

$$
S_t=\frac{\text{forward pitch advancement in metres}}{\text{seconds from recovery to terminal action}}
$$

Terminal action is a shot, turnover, backward reset, or 15-second timeout.

#### Pressure indicator

MVP pressure is a transparent heuristic, not a tracking-data physical-pressure claim:

$$
Q_t=0.30z(\text{final-third entries})+0.25z(\text{progressive actions})+0.20z(\text{territory})+0.15z(\text{shots})+0.10z(\text{possession share})
$$

Each $z$ is clamped to $[0,1]$ against documented scenario-calibrated bounds. UI labels it **attacking pressure indicator**.

#### Rhythm-change indicator

Compare current 60-second intensity and transition count against the preceding five-minute baseline. Require participation by both teams to label a match-wide rhythm change.

### 7.3 Versioning

Metric formulas live in versioned configuration. Every snapshot and insight records `metric_version`. Formula changes require new golden fixtures and cannot silently alter past recaps.

---

## 8. Insight detector

### 8.1 Detector catalogue

Each detector is pure given state and window. It returns zero or one candidate with evidence.

| Tier | Type | Trigger summary | Cooldown | Cancellation |
| --- | --- | --- | ---: | --- |
| Core | `sustained_pressure` | Pressure thresholds plus terminal shot or repeated entry | 120 s/team | Possession loss lasting > 20 s |
| Core | `rhythm_change` | Territory changes team and current intensity/attacks materially exceed baseline | 180 s/match | Signal falls below 1.25× baseline |
| Core | `player_influence` | Sufficient sample, ≥ 60% progression involvement, key contribution | 180 s/player | Player leaves pitch |
| Stretch | `counterattack` | Recovery-to-shot ≤ 12 s and ≥ 35 m advancement | 60 s/team | Terminal turnover before entry |
| Stretch | `tactical_shift` | Tactical event plus sustained channel/territory change | 300 s/team | Reversion before minimum window |

Thresholds are initial calibration values, not universal football truth. They remain externalized and evaluated against scenario labels.

### 8.2 Confidence

Confidence is deterministic:

$$
C=0.40S+0.25D+0.20K+0.15Q
$$

- $S$: threshold strength beyond minimum.
- $D$: evidence diversity across independent metric families.
- $K$: completeness of required context.
- $Q$: source/event quality, equal to 1 for valid simulator events.

Clamp $C$ to $[0,1]$. Display rules:

- `< 0.65`: retain for diagnostics; do not show.
- `0.65–0.79`: medium confidence; cautious language.
- `0.80–0.89`: high confidence.
- `≥ 0.90`: very high confidence; still no certainty wording beyond evidence.

### 8.3 Priority

Priority score combines match impact, recency, novelty, confidence, and user relevance. Map scores to low/medium/high/critical. Only goals or match-ending moments may be critical in MVP. User preference can change ordering but cannot change confidence.

### 8.4 Deduplication

Create a fingerprint from match, type, team/player scope, and overlapping trigger events. Suppress identical fingerprints and candidates inside cooldown unless confidence improves by at least 0.10 or new evidence includes a terminal event. Updated insights reference the superseded insight.

### 8.5 Deterministic copy

Every detector supplies a title and summary template generated from validated evidence. This text appears immediately and remains the fallback if all model services are unavailable.

---

## 9. Agent workflow

### 9.1 Agent boundaries

| Stage | Implementation | Responsibility | Must not do |
| --- | --- | --- | --- |
| Event analyst | Deterministic application executor | Validate candidate and construct semantic insight | Generate prose or infer unsupported tactics |
| Tactical analyst | Foundry agent | Explain bounded football significance | Add metrics, events, or entities |
| Narrative agent | Foundry agent | Produce concise fact-preserving story | Change interpretation or scope |
| Personalization agent | Foundry agent | Adapt emphasis/detail to audience/player | Invent player contribution |
| Localization | Azure AI Translator + glossary validator | Translate approved narrative | Reinterpret football |
| Commentary | Azure Speech adapter | Synthesize approved localized speech text | Generate new words or facts |
| Recap agent | Foundry agent | Rank and connect stored supported insights | Analyze events not represented by selected evidence |

This is purposefully agentic without turning deterministic calculations or service adapters into fake agents.

### 9.2 Workflow sequence

1. Validate `SemanticInsight` against schema.
2. Tactical analyst returns structured `TacticalInterpretation`.
3. A validator checks entities, evidence references, numbers, and temporal scope.
4. Narrative and personalization execute as explicit sequential workflow steps.
5. Validate final claims against the evidence allowlist.
6. Localize only the approved text.
7. Validate protected names, numbers, units, and glossary terms.
8. Publish text to UI.
9. Synthesize/cached speech on request or when safe auto-play is enabled.
10. Persist provenance, latency, token usage, validation result, and fallback.

A failed stage does not retry blindly. Use one retry for transient service errors and one repair attempt for invalid structured output. Then fall back to the last valid representation.

### 9.3 Microsoft Agent Framework implementation rules

- Use the Microsoft Agent Framework Azure AI package and `AIProjectClient` for Foundry agents.
- The Python package is preview at this baseline; installation must include `--pre`.
- Model deployment name, endpoint, and agent IDs are configuration, never hardcoded.
- Structured output schemas mirror versioned Pydantic models.
- Every workflow invocation has a timeout, cancellation token, trace ID, and cost budget.
- Prompts are versioned files with unit/evaluation coverage.
- Foundry model selection is a deployment gate: evaluate current available models for structured-output reliability, multilingual quality, latency, and cost before pinning a deployment.
- Do not use persistent conversation memory for live insights; each call receives a bounded semantic payload. This prevents stale-match contamination.

### 9.4 Grounding validator

The validator tokenizes every generated claim and enforces:

- All entity IDs resolve to the match roster.
- All numbers occur in permitted evidence or are non-statistical clock/score values supplied in context.
- Comparative terms such as “more,” “majority,” and “increased” require explicit comparative evidence.
- Whole-match terms such as “dominated the match” are forbidden for short windows.
- Player attribution requires player-scoped evidence.
- Certainty language is limited by confidence band.
- Tactical labels must belong to the detector's allowed interpretation vocabulary.

Rejected output is never partially displayed.

### 9.5 Cost and latency controls

- Maximum one enhancement workflow per emitted insight.
- Cache by insight fingerprint, audience, prompt version, and language.
- Do not send raw event arrays unless referenced evidence requires them; cap evidence samples.
- Use a low-latency capable deployment for live narratives and a stronger configurable deployment only for full-time recap if evaluation justifies it.
- Limit recap input to ranked insights and final aggregates.
- Record per-match model calls, tokens, latency, and estimated cost.

---

## 10. Personalization modes

### Casual

- Maximum 35 words for live body.
- Plain football language and one key reason.
- No unexplained composite metrics.

### Analyst

- Maximum 90 words.
- Include window, relevant metrics, channel/phase, and calibrated tactical interpretation.
- Show formula/tooltips for heuristic indicators.

### Player

- Requires `selected_player_id`.
- Prioritize selected-player evidence and explicitly state when the player was not materially involved.
- Never reshape a team insight into a player claim without player-scoped evidence.
- Maximum 60 words.

### Metric focus

Metric selection ranks relevant insights and evidence but does not remove critical match events. It is a secondary preference, not a separate audience mode.

### Personalization acceptance

Representations must differ in vocabulary, length, evidence density, and emphasis while preserving entities, numbers, confidence, and semantic conclusion.

---

## 11. Tiered multilingual text and speech

### 11.1 Language delivery matrix

| Tier | Language | Locale | Direction | Delivery expectation |
| --- | --- | --- | --- | --- |
| Tier 1 | English | `en-GB` | LTR | Source/reference text; required |
| Tier 1 | Kiswahili | `sw-KE` | LTR | Required localized text; primary demo language |
| Tier 1 (late) | Kiswahili speech | `sw-KE` | LTR | Required speech demonstration, implemented only after text and recap are stable |
| Tier 2 | French | `fr-FR` | LTR | Next text/speech locale |
| Tier 3 | Spanish | `es-ES` | LTR | Time-permitting breadth |
| Tier 3 | Portuguese | `pt-BR` | LTR | Time-permitting breadth |
| Tier 3 | Arabic | `ar-SA` | RTL | Time-permitting breadth; requires RTL review |
| Tier 3 | Hindi | `hi-IN` | LTR | Time-permitting breadth |

Seven languages remain the design target, not a dependency of the football-intelligence pipeline. The language selector exposes only locales that pass preservation and listening smoke tests. Azure Speech voice support and exact neural voice names must be checked in the deployment region during provisioning. Voice IDs are configuration. If a preferred voice is unavailable, select another voice for the same locale; if no acceptable voice exists, keep localized text available and visibly report speech as unavailable rather than substituting the wrong language.

### 11.2 Localization pipeline

1. Start with validated English semantic narrative.
2. Replace names and protected tokens with placeholders.
3. Translate using Azure AI Translator.
4. Apply the reviewed football glossary.
5. Restore protected tokens.
6. Validate entity names, numbers, score, clock, units, and sentence count.
7. Run language-specific fixtures and optional human review before release.
8. Store translation keyed by source hash, locale, glossary version, and translator version.

### 11.3 Speech pipeline

1. Use validated localized `speech_text`, never raw model output.
2. Escape content and construct restricted SSML server-side.
3. Apply locale, configured neural voice, conservative rate, and sentence pauses.
4. Synthesize to compressed browser-compatible audio.
5. Store audio in Blob Storage with content hash metadata and short-lived read access.
6. Return duration, locale, voice, and artifact URI.
7. Cache exact text/voice/style combinations.

Do not auto-play by default. Respect browser policies, mute state, user preference, and `prefers-reduced-motion` where commentary is coordinated with visual animation. Provide play/pause, replay, volume, and transcript controls.

### 11.4 Speech safety and failure behavior

- Maximum live clip length: 20 seconds.
- Maximum recap clip length: 120 seconds or sectioned playback.
- Cancel stale queued commentary when a higher-priority insight supersedes it.
- Never overlap clips.
- Translation failure falls back to English text and English speech only after explicit UI disclosure.
- Speech failure preserves localized text and exposes retry.
- Arabic UI uses mirrored layout where appropriate, not a globally mirrored pitch orientation.

---

## 12. Post-match recap

Post-match recap is a mandatory MVP feature, generated from retained significant insights and final deterministic aggregates.

### 12.1 Recap selection

At full time:

1. Exclude suppressed, invalid, and low-confidence insights.
2. Group duplicates and retain strongest/latest evolution.
3. Rank by impact, confidence, novelty, match phase, and selected viewer relevance.
4. Select 5–8 moments with first- and second-half coverage where available.
5. Add final team/player aggregates from deterministic metrics.
6. Pass only this recap packet to the recap agent.

### 12.2 Required recap variants

- Casual: 150–250 words.
- Analyst: 300–500 words with evidence-rich sections.
- Player: 180–300 words focused on the selected player, including an explicit “insufficient evidence” state.
- English and Kiswahili text for Tier 1.
- Speech for the demonstrated localized path in Tier 2.
- French and remaining languages inherit the same recap contract only after their live-narrative validation passes.

### 12.3 Recap structure

- Final score and one-sentence match story.
- First-half pattern.
- Second-half pattern.
- Turning point.
- Team comparison.
- Selected-player section when applicable.
- Three evidence-backed key moments.
- Synthetic data disclosure.

The recap must not introduce a new statistical claim. Every claim maps to a final aggregate or stored insight evidence.

---

## 13. API and real-time protocol

### 13.1 REST endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/matches` | Create match from scenario and seed |
| `GET` | `/api/v1/matches/{match_id}` | Match metadata and lifecycle |
| `POST` | `/api/v1/matches/{match_id}/start` | Start an idempotent match |
| `POST` | `/api/v1/matches/{match_id}/pause` | Pause simulation |
| `POST` | `/api/v1/matches/{match_id}/resume` | Resume simulation |
| `POST` | `/api/v1/matches/{match_id}/stop` | Stop match |
| `GET` | `/api/v1/matches/{match_id}/state` | Latest state snapshot |
| `GET` | `/api/v1/matches/{match_id}/events` | Paginated events after sequence |
| `GET` | `/api/v1/matches/{match_id}/insights` | Paginated semantic insights |
| `POST` | `/api/v1/matches/{match_id}/narratives` | Get/generate audience representation |
| `POST` | `/api/v1/matches/{match_id}/speech` | Get/generate speech artifact |
| `GET` | `/api/v1/matches/{match_id}/recap` | Retrieve recap variant |
| `GET` | `/api/v1/scenarios` | Available scenario fixtures |
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | Dependency readiness |

Lifecycle mutations accept `Idempotency-Key`. Errors use one envelope containing code, message, trace ID, retryability, and validation details. MVP permits anonymous demo access but applies origin restrictions, per-IP rate limits, bounded payloads, and non-guessable IDs.

### 13.2 WebSocket

Endpoint: `/api/v1/ws/matches/{match_id}`

Server message types:

- `connection.ready`
- `match.snapshot`
- `match.status`
- `event.created`
- `state.updated`
- `insight.created`
- `insight.updated`
- `narrative.ready`
- `speech.ready`
- `recap.ready`
- `feed.delayed`
- `error`
- `heartbeat`

Every message contains protocol version, message ID, match ID, monotonically increasing stream sequence, server time, correlation ID, type, and payload.

The client reconnects with `after_sequence`. The server replays retained messages or sends a complete snapshot if the replay window is unavailable. Clients deduplicate by message ID and ignore older state revisions. Heartbeat interval is 15 seconds; declare disconnected after two missed heartbeats.

### 13.3 Backward compatibility

- Version REST in the URL and messages in the envelope.
- Additive optional fields are permitted within v1.
- Removing/changing semantics requires v2.
- JSON schemas run in producer and consumer contract tests.

---

## 14. Persistence design

The application uses repository ports from day one. Phase 1 uses an in-memory repository plus optional JSON fixture/export files. This is sufficient for one deterministic demo match and recap development. Azure persistence is introduced only after the canonical path is stable.

### 14.1 Azure Cosmos DB production-path containers

| Container | Partition strategy | Contents | Retention |
| --- | --- | --- | --- |
| `matches` | `/matchId` | Match metadata, state snapshots, lifecycle | 30 days demo |
| `events` | `/matchId` | Ordered event documents | 7 days demo |
| `insights` | `/matchId` | Semantic insights and provenance | 30 days demo |
| `recaps` | `/matchId` | Recap variants and evidence references | 30 days demo |

This schema is the Tier 4 Azure persistence target, not a Phase 1 requirement. The match ID aligns with dominant access patterns: live replay, match timeline, insight list, and recap. Hackathon scale is intentionally small; monitor the single-live-match logical partition for hot-partition symptoms before expanding concurrency. Documents remain well below the 2 MB item limit; event arrays are not embedded into a match item.

Implementation requirements:

- Reuse one async Cosmos client per process.
- Use parameterized point reads and match-scoped queries.
- Handle `429` responses using SDK retry-after behavior and log diagnostics for slow/unexpected responses.
- Use optimistic concurrency through ETags for mutable match state.
- Use the Cosmos DB Emulator only when the Cosmos adapter is implemented; do not require it for ordinary local development.
- Review Request Units and indexing after performance tests; exclude large unused generated text fields from indexing where appropriate.

### 14.2 Blob Storage

When implemented, store speech audio, downloadable broadcast JSON, and optional recap exports. Before then, use short-lived in-process/browser artifacts. Blob paths include environment/match/locale/content hash. Apply lifecycle deletion, private containers, content types, and no permanent public URLs.

### 14.3 Event retention and replay

In local mode, the ordered match event list is the replay source. When Event Hubs and durable storage are implemented, Event Hubs remains transport rather than the system of record. Accepted events are persisted before acknowledgment of durable processing. State snapshots occur every 100 events and at period boundaries. Recovery loads the latest snapshot then replays later events.

---

## 15. Web experience

### 15.1 Primary screen

- Header: synthetic badge, connection state, scenario, live/full-time status.
- Scoreboard: teams, score, period, clock.
- SVG pitch: players, ball, pass/shot trail, formation labels, selected player.
- Insight rail: latest priority-sorted insight with confidence and mode-aware narrative.
- Evidence drawer: window, formulas, metrics, event references, and “synthetic” provenance.
- Event timeline: virtualized, filterable, keyboard navigable.
- Controls: mode, team, player, metric, language, text/speech, play/pause, simulation speed.
- Match rhythm and pressure indicator: clearly labeled heuristic visualizations.
- Full-time recap tab.

### 15.2 Required UI states

- Initial/no match.
- Creating and starting.
- Live and connected.
- Paused.
- Feed delayed.
- Reconnecting.
- AI enhancement pending.
- Deterministic fallback active.
- Translation unavailable.
- Speech unavailable/synthesizing/playing.
- Full time/recap pending/recap ready.
- Fatal match error with reset action.

### 15.3 Accessibility

- Keyboard access for every control and drawer.
- Visible focus and logical focus movement.
- Screen-reader live region for high-priority insights, with user-controlled verbosity.
- Text alternative for pitch events and all charts.
- No information encoded by color alone.
- WCAG AA contrast.
- Reduced animation when requested.
- Captions/transcript always available for speech.
- Correct `lang` and `dir` attributes for localized content.
- Audio never auto-plays until the user opts in.

### 15.4 Client state

Use server state keyed by match/state revision and local UI preferences separately. The browser must never calculate authoritative metrics or confidence. Optimistic updates are allowed only for user controls, not match facts.

---

## 16. Security, privacy, and responsible AI

### 16.1 Security controls

- Managed identity between Azure resources.
- Key Vault for secrets not supported by identity.
- HTTPS/WSS only and strict allowed origins.
- Private storage containers and short-lived artifact access.
- Validate all payloads at trust boundaries.
- Rate-limit expensive narrative/speech/recap operations.
- Escape rendered content; never inject generated HTML.
- Restrict SSML tags and attributes through a server-side builder.
- Pin dependencies and scan Python, npm, containers, IaC, and repository secrets.
- Separate dev/test/demo environments and resource identities.
- Do not log access keys, full tokens, or unnecessary request content.

### 16.2 Privacy baseline

MVP has no login and intentionally collects no personal profile data. Session preferences remain browser-local. Operational logs may contain IP-derived platform metadata and therefore follow the configured short retention and access policy. Document telemetry disclosure in the UI/README.

### 16.3 Responsible AI controls

- Visible “Synthetic Match” label at all times.
- Visible “AI-enhanced” and fallback provenance where appropriate.
- No unsupported statistics or player claims.
- No sensitive personal inference; all players are fictional.
- Confidence-aware wording.
- Complete audit chain from narrative claim to evidence.
- Agent input uses allowlisted structured fields, limiting prompt injection surface.
- User-provided free text is not part of MVP.
- Localization does not alter evidence or confidence.
- Human-reviewed golden examples cover every locale enabled in the submitted UI.

---

## 17. Observability and operations

### 17.1 Correlation

Propagate `trace_id`, `match_id`, `event_id`, `insight_id`, and `workflow_run_id` through logs, Event Hubs properties, persistence, agent calls, WebSocket messages, and browser telemetry.

### 17.2 Metrics

Track:

- Events generated/accepted/rejected and consumer lag.
- Processing and detector latency percentiles.
- State revision and replay count.
- Candidate/emitted/suppressed insight counts by type.
- Agent calls, latency, schema failures, grounding rejections, retries, tokens, and fallback rate.
- Translation/speech latency, cache hit, failures, and locale.
- WebSocket connections, reconnects, replay gaps, and outbound queue size.
- Cosmos request charge, throttles, diagnostics-triggering latency, and failures.
- End-to-end event-to-render latency.
- Per-match estimated cloud/AI cost.

### 17.3 Alerts

For the demo environment alert on:

- No events for 10 seconds during a running match.
- Event consumer lag over 2 seconds.
- AI fallback rate over 20% in 15 minutes.
- Any grounding rejection spike.
- Speech or translation failures over 10%.
- WebSocket disconnect rate over threshold.
- Cosmos throttling or readiness failure.

### 17.4 Runbooks

Create runbooks for local startup, Azure deployment, stale Event Hub consumer, Cosmos throttling, Foundry outage, Translator/Speech outage, WebSocket storm, demo reset, and full resource teardown.

---

## 18. Testing and evaluation

### 18.1 Test pyramid

The catalogue below describes the desired coverage. It is implemented incrementally: tests for a capability become required when that capability enters its delivery tier. Phase 1 is blocked only by contract, metric, detector, simulator-invariant, build, and canonical end-to-end failures.

| Layer | Coverage |
| --- | --- |
| Unit | Metric formulas, state transitions, detector thresholds, validators |
| Property | Simulator invariants, event ordering, idempotency, coordinate bounds |
| Golden | Seeded scenarios, exact evidence, expected/forbidden insights |
| Contract | JSON Schema, OpenAPI, WebSocket messages, generated TypeScript types |
| Integration | Queue/Event Hubs adapters, Cosmos emulator, Foundry fakes, Translator/Speech fakes |
| Agent evaluation | Grounding, tactical relevance, schema validity, personalization |
| Localization | Entity/number preservation, glossary, direction, meaning rubric |
| UI | Components, accessibility, mode/language/player switching |
| End-to-end | Entire MVP judge journey and degraded variants |
| Performance | Event rate, stream fan-out, insight latency, recap latency |
| Security | Dependency/container/IaC scans, rate limits, input and SSML abuse |

### 18.2 Golden scenario acceptance

For each required scenario:

- Replay 20 fixed seeds.
- Expected detector recall: 100% on scripted signal seeds.
- Forbidden unrelated high-priority insights: zero.
- Trigger must occur within its documented window.
- Evidence values must match independently calculated fixtures.
- Replaying a seed must yield the same ordered events and insights.

Additional neutral/noise scenarios test false positives. Initial release target is ≥ 95% precision on the labeled synthetic evaluation set.

### 18.3 Agent grounding evaluation

Each case supplies semantic insight and expected constraints. Score:

- JSON/schema validity: 100%.
- Entity preservation: 100%.
- Numerical faithfulness: 100%.
- Temporal scope preservation: 100%.
- Unsupported tactical claims: 0 critical.
- Useful interpretation by reviewer rubric: ≥ 4/5 average.

A release fails on any fabricated number or unsupported player attribution.

### 18.4 Personalization evaluation

For the same insight, verify:

- Casual is shorter and simpler than analyst.
- Analyst contains more evidence and tactical detail.
- Player mode includes selected-player evidence or an honest no-material-involvement message.
- No mode changes the factual conclusion, numbers, or confidence.

### 18.5 Localization and speech evaluation

For every locale:

- Preserve team/player names, score, numbers, units, and time window exactly.
- Validate glossary terms using reviewed fixtures.
- Use correct direction and Unicode rendering.
- Verify text-to-speech produces playable audio with matching locale metadata.
- Human-review at least 20 representative live narratives and five recaps for each Tier 1 locale; use a smaller smoke set while qualifying higher-tier locales.
- Confirm failure fallback never silently switches language.

### 18.6 Resilience tests

Inject duplicate/out-of-order/invalid events, Event Hub delay, Cosmos `429`, Foundry timeout, invalid agent JSON, grounding rejection, translation failure, speech failure, WebSocket disconnect, and browser refresh. Verify explicit status and documented fallback for each.

### 18.7 Release gates

A submission-critical release passes only when:

- Critical unit, contract, build, and canonical end-to-end tests pass.
- Three core golden scenarios and their key simulator invariants pass.
- Zero critical grounding defects remain.
- English/Kiswahili preservation tests and the demonstrated speech path pass.
- Any additional enabled locale passes its own text/speech smoke tests; an unvalidated locale remains hidden rather than blocking Tier 1.
- Accessibility automated checks and manual MVP keyboard journey pass.
- p95 latency targets pass under the demo load profile.
- Security scans have no unresolved critical/high findings.
- The minimal Azure demo smoke test passes.

Property breadth, failure injection, complete performance coverage, all Azure adapters, and production operational tests remain valuable Tier 4 work. They become release blockers only for components actually enabled in the submitted experience.

---

## 19. Delivery plan

The sequence below is binding. Calendar dates are target boundaries, not invitations to expand scope. If a phase slips, cut higher-tier breadth first.

### Phase 0 — Foundation (October 1–2)

Build only what the first slice needs:

- Repository workspaces and minimal CI.
- Event, match-state, metric, insight, and scenario contracts.
- Generated Python/TypeScript types.
- Domain/application/infrastructure boundaries.
- One sustained-pressure fixture and deterministic seed.
- Local run commands and test harness.

**Exit gate:** contracts validate in Python and TypeScript, the fixture loads, and both project skeletons build.

### Phase 1 — “It works” vertical slice (October 3–7)

Build:

```text
seeded simulator
  ↓
local event stream
  ↓
match-state reducer
  ↓
90-second metrics
  ↓
sustained-pressure detector
  ↓
evidence-backed insight
  ↓
Next.js pitch, timeline, insight, and Why? drawer
```

Explicitly exclude Foundry, translation, speech, Cosmos DB, Event Hubs, and recap from this phase.

**Exit gate:** from a clean start, a judge can run the canonical seed, see **Pressure Building**, open **Why?**, and inspect the correct events and metrics. This is the first protected demo checkpoint.

### Parallel risk spike — Foundry and Azure (October 3–6, time-boxed)

In a disposable path, prove one structured Foundry response, one Container Apps deployment, and one Application Insights trace. Spend no more than half a day per unresolved blocker before documenting it and returning to Phase 1. Do not merge spike shortcuts into the domain.

**Exit gate:** known endpoint, identity, quota, region, SDK, and hosting risks have owners and fallback decisions.

### Phase 2 — Core football intelligence (October 8–11)

Add only:

- Momentum/rhythm-shift scenario and detector.
- Player-influence scenario, player metrics, and detector.
- Confidence, priority, cooldown, and deduplication.
- Golden replay and forbidden-insight tests for the three core patterns.
- UI confidence, selected-player state, and evidence refinements.

Counterattack and tactical shift remain stretch until this phase is green.

**Exit gate:** all three core scenarios replay deterministically and pass evidence/false-positive checks.

### Phase 3 — Foundry explanation (October 12–15)

Add:

- Current-model evaluation and deployment selection record.
- Tactical, narrative, and personalization agent contracts.
- Microsoft Agent Framework workflow through `AIProjectClient`.
- Structured output, grounding validator, prompt versioning, timeout, trace, and deterministic fallback.
- Minimal Azure deployment of the real vertical slice by the end of the phase.

Start with one sequential workflow. Merge agent responsibilities if multiple calls add latency without measurable quality.

**Exit gate:** the deterministic insight appears immediately; a grounded AI-enhanced narrative follows; Azure hosts the same canonical path; forced Foundry failure preserves the deterministic experience.

### Phase 4 — Personalization (October 16–17)

Add fan, analyst, and player representations of the same semantic insight. Player mode must use player-scoped evidence or explicitly say the selected player was not materially involved.

**Exit gate:** mode outputs are visibly different in detail and emphasis while entities, numbers, confidence, and conclusion remain identical.

### Phase 5 — Localization, speech, and recap (October 18–20)

In order:

1. English source text.
2. Kiswahili text with protected entities and glossary validation.
3. Insight retention and a concise English/Kiswahili post-match recap.
4. Kiswahili speech for the demonstrated insight and recap path.
5. French text/speech only if steps 1–4 are stable.
6. Remaining locales only as Tier 3 work.

**Exit gate:** English/Kiswahili live narratives and recap preserve evidence; one localized speech path plays reliably with transcript and text fallback. **Feature freeze begins at the end of October 20.**

### Phase 6 — Integration and cloud polish (October 21–23)

- Stabilize Container Apps deployment and managed configuration.
- Add only the Azure services required by the actual implementation; prefer simple storage/queue adapters when cloud services do not improve the demo.
- Add correlation traces and a small architecture/technology view.
- Run critical unit, contract, golden, grounding, localization, accessibility, and end-to-end tests.
- Measure demo-path latency and fix visible regressions.

**Exit gate:** deployed canonical journey passes twice from a clean browser, and the local fallback remains runnable.

### Phase 7 — Submission hardening (October 24–27)

- No new features.
- Fix only release-blocking defects.
- Finalize README, setup, architecture diagram, synthetic-data disclosure, license, and attribution.
- Validate public repository secret hygiene.
- Rehearse and record the under-two-minute demo.
- Keep a deterministic fallback recording/path ready.
- Complete final rule and submission checklist review.

**Exit gate:** submission artifacts are uploaded before the deadline, the deployed demo is healthy, and the repository reproduces the core path.

---

## 20. CI/CD and environment strategy

### 20.1 Environments

- `local`: in-process queue, file/in-memory persistence, and fake/optional cloud adapters; this is the fastest development and fallback path.
- `test`: local automated dependencies and recorded/fake cloud responses where practical.
- `demo`: smallest viable Azure deployment with min replicas during scheduled demonstrations.

### 20.2 Pull-request pipeline

Required from Phase 0:

1. Secret scan.
2. Python formatting, linting, type checking, and critical unit/contract tests.
3. TypeScript formatting, linting, type checking, component smoke tests, and build.
4. Schema generation drift check.
5. Canonical deterministic end-to-end smoke test once Phase 1 lands.

Add after the relevant capability exists:

1. Grounding and personalization smoke evaluation.
2. English/Kiswahili preservation tests.
3. Container build and vulnerability scan before cloud integration.
4. Bicep validation only for infrastructure actually implemented.

Long-running property, resilience, performance, and complete evaluation suites may run nightly or before release; they must not slow the Phase 1 feedback loop.

### 20.3 Deployment pipeline

- Validate the implemented infrastructure subset.
- Provision/update the minimal demo environment.
- Apply role assignments and configuration references.
- Deploy immutable container digests.
- Run persistence initialization idempotently when durable storage is present.
- Run only capability-relevant health, event flow, Foundry, translation, speech, recap, and WebSocket smoke tests.
- Promote only after smoke tests pass.
- Roll back container revisions on failure; infrastructure changes require explicit review.

---

## 21. Demo implementation

### 21.1 Canonical two-minute flow

1. **0:00–0:15:** Open the project; point out the synthetic-data label and start the canonical `sustained_pressure` seed.
2. **0:15–0:30:** Let the pitch and event timeline establish that a live sequence is running.
3. **0:30–0:40:** Show **Pressure Building** as soon as the detector fires.
4. **0:40–0:55:** Open **Why?** and connect the claim to the window, metrics, and event IDs.
5. **0:55–1:10:** Reveal the grounded Foundry-enhanced explanation and briefly identify the deterministic fallback.
6. **1:10–1:25:** Switch fan → analyst to demonstrate deeper presentation of identical facts.
7. **1:25–1:40:** Switch English → Kiswahili and play one short spoken clip.
8. **1:40–1:52:** Select the influential player and show evidence-scoped player mode.
9. **1:52–1:58:** Jump to full time and flash the evidence-backed recap.
10. **1:58–2:00:** End on the compact architecture/Microsoft technology strip.

Do not cycle through every language, detector, Azure resource, or test. The demo proves the idea; the README and architecture artifact show breadth.

### 21.2 Preflight checklist

- Demo environment healthy and cost budget enabled.
- Foundry, Translator, Speech, hosting, and every Azure dependency actually used by the demo pass readiness checks.
- Demo seed exists and golden output matches deployed detector version.
- The configured demo voice passes a one-line synthesis test; all other enabled voices pass their own smoke test.
- Browser audio permission tested.
- Warm one narrative, translation, and voice cache without fabricating match state.
- Reset endpoint clears previous demo match.
- Backup deterministic-only mode and pre-generated matching speech artifacts are available and disclosed if used.
- Screen recording resolution, zoom, captions, and timer checked.

---

## 22. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Agent invents a statistic | Critical trust failure | Structured evidence allowlist, claim validator, hard fallback |
| Seven-language scope weakens core | Delivery delay | Tier locales, expose only validated languages, cut breadth before core quality |
| Speech voice unavailable in region | Missing MVP language | Provisioning gate, configurable voice/region, no silent substitution |
| Sequential agents exceed latency | Poor live experience | Immediate deterministic copy, bounded payloads, caching, strict timeout |
| Simulator appears unrealistic | Weak credibility | Football invariants, scripted scenarios, transparent heuristics, review |
| Pressure/momentum overclaimed | Misleading output | Label as indicators, publish formulas, calibrated vocabulary |
| Player mode attributes team behavior | Unsupported claim | Require player-scoped evidence or show insufficient evidence |
| Recap adds new claims | Grounding failure | Generate only from ranked insights/final aggregates and revalidate |
| Event duplicates/out-of-order delivery | Incorrect metrics | Idempotency, sequence authority, tolerance/dead-letter, replay tests |
| One match creates Cosmos hot partition | Throttling | Monitor RU/diagnostics; MVP bounded load; revisit key at concurrency expansion |
| Cloud outage disrupts demo | Demo failure | Local/in-process profile, deterministic fallbacks, cached valid artifacts |
| Public repository leaks credentials | Security incident | Managed identity, Key Vault, secret scans, `.env.example` only |
| Azure cost grows unexpectedly | Budget failure | Scale-to-zero outside demo, concurrency caps, budgets/alerts, teardown |
| Model/service API changes | Build failure | Adapter boundaries, pinned versions, deployment smoke tests |
| RTL breaks layout | Poor Arabic UX | Direction-aware component tests and manual accessibility review |

---

## 23. Definition of done

The submission-critical MVP is done only when all statements are true:

- A clean clone can run locally from documented steps.
- The minimal Azure deployment runs the canonical journey and documents its implemented services.
- Three seeded core scenario families produce correct, reproducible event streams.
- Metrics and insights match golden evidence.
- Every displayed generated claim passes grounding validation.
- Casual, analyst, and player modes are materially different but factually identical.
- Player mode never attributes unsupported actions.
- English and Kiswahili pass text preservation gates, and the demonstrated localized speech path is reliable.
- A grounded post-match recap is available in English and Kiswahili; only validated additional locales are enabled.
- The UI handles the failure states exercised by the canonical journey and retains deterministic fallbacks.
- The MVP keyboard journey, visible focus, transcript, basic latency target, secret scan, and cost guardrails pass.
- The public repository contains no proprietary data, footage, or secrets.
- The under-two-minute demo demonstrates the complete value chain.

Tier 3 breadth and Tier 4 production-path work are successful additions, not conditions for calling the hackathon MVP complete.

---

## 24. Immediate implementation backlog

Execute in this order:

1. Create repository workspaces, CI, and local configuration.
2. Write only the contracts needed for `Event`, `MatchState`, `MetricSnapshot`, `SemanticInsight`, and the first scenario; generate Python/TypeScript types.
3. Time-box a disposable Foundry structured-output and Container Apps authentication/deployment spike in parallel.
4. Implement the sustained-pressure scenario, seeded simulator, invariants, event reducer, and 90-second metrics.
5. Expose minimal match lifecycle REST and versioned WebSocket messages.
6. Build the pitch, timeline, insight card, and **Why?** evidence drawer.
7. Protect this deterministic vertical slice with a canonical end-to-end test.
8. Add momentum/rhythm-shift and player-influence scenarios, metrics, detectors, confidence, and priority.
9. Add the real Foundry workflow, validator, traces, and deterministic fallback.
10. Add fan, analyst, and player representations.
11. Add English/Kiswahili localization, then grounded post-match recap.
12. Add one reliable localized speech path; add French and remaining locales only by tier.
13. Integrate the minimal Azure demo path and only the cloud adapters that improve it.
14. Freeze features on October 20 and finish critical evaluation, accessibility, security, documentation, rehearsal, and recording.

The first coding milestone is complete when a seeded sustained-pressure scenario creates a validated evidence-backed insight in the browser without using an LLM. This proves the trustworthy foundation on which the agentic, multilingual, spoken, and recap experiences depend.

---

## 25. Deferred decisions and decision gates

The following choices must be resolved through measured implementation rather than assumption:

| Decision | Gate | Evidence required |
| --- | --- | --- |
| Foundry model deployment(s) | Before Phase 3 completion | Current project availability, structured-output eval, multilingual quality, p95 latency, cost |
| Exact speech voice per enabled locale | Before Phase 5 completion | Regional availability and listening review |
| Event Hubs partition count and retention | Before demo deployment | Measured event rate and replay requirement |
| Cosmos throughput mode/index policy | Before cloud load test | RU profile, query plan, throttling results |
| Agent split/merge optimization | After first end-to-end trace | Quality difference versus added latency/cost |
| Auto-play default | UX/accessibility review | Browser behavior and user testing; baseline remains off |

Any scope change that threatens the deterministic vertical slice, grounding, player mode, English/Kiswahili delivery, the demonstrated speech path, recap, or October 20 feature freeze requires explicit revision of this document and its release gates.
