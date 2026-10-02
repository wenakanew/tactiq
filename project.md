# tactiq--- AI Match Intelligence & Personalized Commentary Engine

> **Project Specification / Architecture Blueprint**
>
> **Hackathon:** Microsoft Premier League Hackathon --- *Inside the
> Game: Developer Hackathon*\
> **Project Status:** Pre-implementation design\
> **Primary concept:** Real-time synthetic football intelligence,
> explainable narratives, personalized experiences, and multilingual
> commentary\
> **Planned platform:** Microsoft Azure + Microsoft Foundry\
> **Planned application:** Web-based live match intelligence experience
> with an agentic backend

------------------------------------------------------------------------

## 1. Project overview

### 1.1 What is tactiq?

**tactiq** is an AI-powered football match intelligence platform
designed to understand the story of a football match as it unfolds.

Rather than treating a match as a stream of isolated events such as
passes, shots, tackles, possession changes, and goals, tactiq
continuously analyzes those events, identifies meaningful patterns,
determines why those patterns matter, and converts them into
personalized match experiences.

The system is designed around a simple principle:

> **Don't just tell the viewer what happened. Tell them what is
> happening, why it matters, and explain it in the way they want to
> receive it.**

A football match generates enormous amounts of low-level event
information. A pass is a pass. A tackle is a tackle. A possession change
is a possession change. Individually, these events often have limited
meaning.

The intelligence appears when those events are considered together.

For example:

-   one progressive pass may not be particularly significant;
-   five progressive passes in 70 seconds may indicate sustained
    attacking pressure;
-   repeated entries into a particular zone may indicate a tactical
    change;
-   a player's involvement may suddenly increase after a formation
    change;
-   a sequence of recoveries and rapid forward passes may indicate the
    beginning of a counter-attacking phase;
-   a shot following a sustained period of territorial pressure may
    represent the culmination of a meaningful attacking sequence.

Tactiq is intended to detect these situations.

The system ingests synthetic football-realistic events, transforms them
into structured football state and statistics, uses specialized agents
to reason over that state, produces explainable insights, and delivers
those insights through a user interface tailored to the viewer.

------------------------------------------------------------------------

## 2. The problem

### 2.1 Football produces data faster than humans can interpret it

Modern football produces a continuous stream of information:

-   passes;
-   pass locations;
-   pass distance;
-   pass success;
-   possession changes;
-   tackles;
-   interceptions;
-   shots;
-   shot speed;
-   ball speed;
-   player movement;
-   pressure;
-   territory;
-   player involvement;
-   substitutions;
-   formations;
-   match state;
-   time;
-   scoreline;
-   and many other contextual events.

A traditional statistics layer can report these numbers.

The difficult problem is interpretation.

A viewer generally does not want a live feed saying:

> Player A completed 7 passes.

They may want to know:

> Player A has become increasingly involved in progressing the ball, and
> his increased involvement is helping his team move higher up the
> pitch.

The difference is the **story behind the statistic**.

------------------------------------------------------------------------

## 3. The gap tactiq is designed to solve

### 3.1 The gap between raw events and meaningful football stories

The core gap is:

``` text
Raw football events
        ↓
Statistics
        ↓
Patterns
        ↓
Context
        ↓
Meaning
        ↓
Personalized explanation
```

Many analytics systems are strong at the first few stages.

Tactiq is designed to focus heavily on the transition from:

> **"What happened?"**

to:

> **"What does it mean?"**

and ultimately:

> **"Why should this particular viewer care?"**

------------------------------------------------------------------------

### 3.2 The gap between statistics and explanation

A dashboard can tell a viewer that:

-   possession increased;
-   pass completion increased;
-   shots increased;
-   pressure increased;
-   a player touched the ball more often.

Those numbers do not automatically explain the relationship between
them.

Tactiq therefore separates **measurement** from **interpretation**.

Deterministic football logic is responsible for calculating measurable
facts.

AI agents are responsible for interpreting those facts, connecting them
to context, generating narratives, and adapting the explanation to the
viewer.

This separation is intentional.

The system should not ask a language model to invent football
statistics.

Instead:

``` text
Events
  ↓
Deterministic calculations
  ↓
Verified football state
  ↓
Detected pattern
  ↓
Agent reasoning
  ↓
Explanation
```

This makes the generated narrative more traceable and reduces the risk
of unsupported claims.

------------------------------------------------------------------------

## 4. The central product idea

Tactiq can be summarized through five questions:

### 4.1 What happened?

The system ingests live synthetic match events.

### 4.2 What is happening?

The system aggregates recent events into football states and patterns.

### 4.3 Why does it matter?

The system identifies the significance of those patterns in the context
of the match.

### 4.4 Who is watching?

The system determines the audience context:

-   casual fan;
-   analyst;
-   player-focused viewer;
-   team-focused viewer;
-   viewer interested in a specific metric;
-   or another configured audience.

### 4.5 How should the information be delivered?

The system adapts the same underlying football intelligence into:

-   concise on-screen insight;
-   detailed analyst explanation;
-   player-focused narrative;
-   multilingual text;
-   and potentially spoken commentary.

------------------------------------------------------------------------

# 5. The proposed solution

## 5.1 High-level architecture

``` text
                  ┌─────────────────────────┐
                  │   SYNTHETIC MATCH       │
                  │       SIMULATOR         │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │      EVENT STREAM       │
                  │ Pass / Shot / Tackle    │
                  │ Possession / Pressure   │
                  │ Player / Match Metadata │
                  └────────────┬────────────┘
                               │
                               ▼
                  ┌─────────────────────────┐
                  │ FOOTBALL INTELLIGENCE   │
                  │        ENGINE           │
                  │                         │
                  │ Statistics              │
                  │ Match state             │
                  │ Pattern detection       │
                  │ Momentum / pressure     │
                  │ Player involvement      │
                  └────────────┬────────────┘
                               │
                               ▼
                 ┌──────────────────────────┐
                 │      AGENTIC LAYER       │
                 │                          │
                 │ Event Analyst             │
                 │ Tactical Analyst          │
                 │ Narrative Agent           │
                 │ Personalization Agent     │
                 │ Localization Agent        │
                 │ Commentary Agent          │
                 └────────────┬─────────────┘
                              │
                              ▼
                 ┌──────────────────────────┐
                 │    EXPERIENCE LAYER      │
                 │                          │
                 │ Live insight              │
                 │ Match visualization       │
                 │ Analyst mode              │
                 │ Fan mode                  │
                 │ Player mode               │
                 │ Language selection        │
                 │ Commentary                │
                 └──────────────────────────┘
```

------------------------------------------------------------------------

# 6. The five-stage challenge pipeline

Tactiq is intentionally designed around the challenge's five-stage
pipeline:

``` text
INGEST → INTERPRET → EXPLAIN → RENDER → PERSONALIZE
```

These stages form the backbone of the system.

------------------------------------------------------------------------

## 6.1 Stage 1 --- Ingest

The system receives synthetic football events as they occur.

Possible events include:

-   pass;
-   shot;
-   tackle;
-   interception;
-   possession change;
-   pressure event;
-   player movement;
-   substitution;
-   formation change;
-   goal;
-   foul;
-   match metadata.

A simplified event might look like:

``` json
{
  "timestamp": "67:42",
  "event_type": "pass",
  "team_id": "team_a",
  "player_id": "player_17",
  "target_player_id": "player_09",
  "start_position": {
    "x": 54.2,
    "y": 42.1
  },
  "end_position": {
    "x": 71.6,
    "y": 36.4
  },
  "distance_m": 18.4,
  "successful": true,
  "progressive": true,
  "zone": "final_third"
}
```

The actual event schema will be refined during implementation.

The important design principle is that every event should contain enough
information to support meaningful downstream analysis.

------------------------------------------------------------------------

# 7. Synthetic match data

## 7.1 Why synthetic data?

The hackathon specifically requires projects to use synthetic,
football-realistic data.

Tactiq therefore will not depend on redistributed proprietary Premier
League match datasets or copyrighted Premier League footage.

Instead, the project will create its own synthetic football environment.

This has several advantages:

1.  The project can be reproduced locally.
2.  The event stream can be controlled.
3.  Specific football situations can be intentionally generated.
4.  Edge cases can be tested.
5.  The system does not depend on a third-party live football feed.
6.  The demo can reliably trigger meaningful insights.
7.  The dataset can be extended as the project evolves.

------------------------------------------------------------------------

## 7.2 Synthetic Match Simulator

The project will include a **Synthetic Match Simulator**.

The simulator represents a football match as a sequence of realistic
events.

It can model:

-   two teams;
-   players;
-   player positions;
-   formations;
-   possession;
-   passing;
-   progressive movement;
-   attacking phases;
-   defensive phases;
-   pressure;
-   shots;
-   tackles;
-   interceptions;
-   substitutions;
-   scoreline;
-   match time;
-   and tactical scenarios.

The simulator does not need to perfectly recreate professional football.

Its purpose is to generate realistic enough event sequences for the
intelligence engine to reason over.

------------------------------------------------------------------------

## 7.3 Scenario-based simulation

The simulator should support predefined scenarios.

### Scenario A --- Sustained pressure

``` text
Possession
   ↓
Progressive pass
   ↓
Final-third entry
   ↓
Short pass
   ↓
Pressure event
   ↓
Another final-third entry
   ↓
Shot
```

Expected insight:

> Team A has been building sustained pressure, with repeated final-third
> entries and increased attacking activity over the previous 90 seconds.

------------------------------------------------------------------------

### Scenario B --- Counterattack

``` text
Opponent possession
       ↓
Turnover
       ↓
Ball recovery
       ↓
Rapid progressive pass
       ↓
Forward movement
       ↓
Final-third entry
       ↓
Shot
```

Expected insight:

> Team B transitioned rapidly from recovery to attack, turning a
> defensive regain into a shot within seconds.

------------------------------------------------------------------------

### Scenario C --- Tactical shift

``` text
Substitution
      ↓
Formation change
      ↓
Different passing lanes
      ↓
Higher territorial control
      ↓
New attacking pattern
```

Expected insight:

> Following the substitution, Team A has shifted its attacking structure
> and is now creating more entries through the left side.

------------------------------------------------------------------------

### Scenario D --- Player influence

``` text
Player X
   ↓
More touches
   ↓
More progressive passes
   ↓
More final-third involvement
   ↓
Key pass
   ↓
Shot
```

Expected insight:

> Player X has become increasingly influential in the attacking phase,
> contributing to the majority of the team's recent progression.

------------------------------------------------------------------------

### Scenario E --- Match rhythm change

``` text
Low event intensity
       ↓
Possession turnover
       ↓
Rapid transitions
       ↓
Multiple attacks
       ↓
Shots
```

Expected insight:

> The match has shifted from a slower possession phase into a more
> transitional period, with both teams moving the ball forward more
> quickly.

------------------------------------------------------------------------

# 8. Football Intelligence Engine

The Football Intelligence Engine is the analytical core of Tactiq.

It sits between the raw event stream and the AI agents.

Its job is to convert low-level events into reliable football state.

------------------------------------------------------------------------

## 8.1 Responsibilities

The engine will calculate or derive:

-   possession;
-   possession changes;
-   pass completion;
-   progressive passes;
-   pass distance;
-   pass difficulty;
-   territory;
-   final-third entries;
-   attacking sequences;
-   pressure;
-   shot frequency;
-   shot speed;
-   ball speed;
-   player involvement;
-   team momentum indicators;
-   recent event intensity;
-   attacking transitions;
-   defensive transitions;
-   passing patterns;
-   player performance trends;
-   match rhythm;
-   and other derived metrics.

------------------------------------------------------------------------

## 8.2 Deterministic first, generative second

A major architectural principle:

> **The LLM should explain verified football intelligence, not
> manufacture it.**

For example:

``` text
Raw events
     ↓
Python/TypeScript calculations
     ↓
"Team A completed 8 progressive passes
 in the final third over 90 seconds."
     ↓
Pattern detector
     ↓
"Potential sustained pressure"
     ↓
AI agent
     ↓
"Team A are beginning to pin the opposition back..."
```

This architecture creates a cleaner boundary between:

-   facts;
-   calculations;
-   interpretation;
-   generation.

------------------------------------------------------------------------

# 9. Insight detection

Not every event should create an AI response.

If every pass generates an LLM call, the system will produce noise,
consume unnecessary resources, and overwhelm the viewer.

Tactiq should therefore include an **Insight Detection Layer**.

------------------------------------------------------------------------

## 9.1 Insight triggers

An insight may be triggered when:

-   pressure crosses a threshold;
-   possession changes rapidly;
-   a team records an unusual sequence;
-   attacking intensity increases;
-   a player becomes significantly more involved;
-   a tactical change alters the team's behavior;
-   a shot follows a sustained attacking phase;
-   a counterattack develops;
-   match rhythm changes;
-   a significant milestone occurs;
-   or several smaller events combine into a meaningful pattern.

------------------------------------------------------------------------

## 9.2 Insight priority

Insights can be assigned priority:

``` text
LOW
MEDIUM
HIGH
CRITICAL
```

Example:

``` json
{
  "insight_type": "pressure_shift",
  "priority": "high",
  "timestamp": "67:42",
  "team": "team_a",
  "confidence": 0.91
}
```

This prevents the interface from displaying every minor observation.

------------------------------------------------------------------------

# 10. Explainability

Tactiq should be able to answer:

> **Why did the system generate this insight?**

Every insight should ideally contain evidence.

Example:

``` json
{
  "title": "Pressure Building",
  "summary": "Team A are beginning to pin Team B back.",
  "confidence": 0.91,
  "evidence": {
    "progressive_passes": 6,
    "final_third_entries": 4,
    "possession_seconds": 71,
    "shots": 1
  },
  "time_window": "66:30-67:42"
}
```

This allows the UI to expose an explanation such as:

> **Why this matters**
>
> Team A completed six progressive passes and entered the final third
> four times in the last 72 seconds, culminating in a shot.

This is much stronger than simply generating:

> "Team A are playing well."

------------------------------------------------------------------------

# 11. Agentic architecture

The project is designed as a purposeful multi-agent system.

The agents are not simply multiple copies of the same chatbot.

Each agent has a distinct responsibility.

------------------------------------------------------------------------

## 11.1 Event Analyst Agent

### Responsibility

Understand the immediate event stream and summarize relevant
developments.

### Inputs

-   recent events;
-   calculated statistics;
-   player information;
-   team information;
-   match clock;
-   scoreline.

### Outputs

Structured observations.

Example:

``` json
{
  "observation": "team_a_pressure_increasing",
  "supporting_metrics": [
    "6 progressive passes",
    "4 final-third entries"
  ]
}
```

------------------------------------------------------------------------

## 11.2 Tactical Intelligence Agent

### Responsibility

Interpret the football meaning of patterns.

It can reason about:

-   territorial control;
-   attacking pressure;
-   transitions;
-   passing patterns;
-   match rhythm;
-   player roles;
-   tactical changes.

Example:

> Team A's repeated entries through the left half-space indicate a shift
> toward attacking that channel.

The agent should receive verified metrics rather than inventing them.

------------------------------------------------------------------------

## 11.3 Narrative Agent

### Responsibility

Turn football intelligence into a coherent story.

It answers:

> What is the story right now?

Examples:

-   pressure is building;
-   a team is losing control;
-   a player is becoming influential;
-   the match has opened up;
-   a counterattack changed the rhythm;
-   a tactical change is beginning to have an effect.

------------------------------------------------------------------------

## 11.4 Personalization Agent

### Responsibility

Adapt an insight to the viewer.

Possible audience modes:

### Casual fan

Short, understandable, conversational.

### Analyst

More detailed and metric-heavy.

### Player-focused

Focuses on a selected player's actions and influence.

### Team-focused

Prioritizes the selected team.

### Metric-focused

Prioritizes one statistic or performance dimension.

The underlying intelligence remains the same.

Only the presentation changes.

------------------------------------------------------------------------

# 12. Multilingual storytelling

Multilingual storytelling is a major planned capability.

The hackathon explicitly identifies multi-language storytelling as a
possible project direction.

Tactiq will therefore separate **football intelligence** from
**language generation**.

------------------------------------------------------------------------

## 12.1 Language-neutral insight layer

Instead of generating an English paragraph immediately, the system
should first produce a structured semantic insight.

Example:

``` json
{
  "event": "pressure_shift",
  "team": "Team A",
  "time_window": "66:30-67:42",
  "summary_concept": "Team A is increasing territorial pressure",
  "evidence": [
    "6 progressive passes",
    "4 final-third entries",
    "1 shot"
  ]
}
```

The localization layer then converts that concept into the selected
language.

This is important because it avoids creating separate football reasoning
pipelines for every language.

------------------------------------------------------------------------

## 12.2 Example

### English

> Team A are beginning to pin the opposition back.

### Kiswahili

> Team A wanaanza kuwasukuma wapinzani wao nyuma.

### Analyst English

> Team A's territorial control has increased over the last 90 seconds,
> driven by repeated progressive entries into the final third.

The intelligence is the same.

The experience is different.

------------------------------------------------------------------------

# 13. Commentary layer

The multilingual system can eventually extend beyond text into spoken
commentary.

The architecture is:

``` text
Football Event
      ↓
Football Intelligence
      ↓
Structured Insight
      ↓
Narrative
      ↓
Language
      ↓
Speech
```

For example:

``` text
User selects:

Language: Kiswahili
Mode: Casual Fan
Output: Voice
```

The system could generate a short natural-language commentary segment.

The first implementation should prioritize text because text is easier
to validate, debug, and demonstrate.

Speech can then be added as an extension.

------------------------------------------------------------------------

# 14. User interface

## 14.1 Why Tactiq needs a UI

The project should not be only a backend script running in a terminal.

A terminal can demonstrate the architecture, but the challenge is
explicitly concerned with rendering insights alongside the match and
creating personalized experiences.

Therefore, Tactiq should have a web interface.

The UI is not intended to reproduce a commercial football broadcast.

It is a demonstration and interaction layer for the intelligence engine.

------------------------------------------------------------------------

## 14.2 Proposed UI

``` text
┌─────────────────────────────────────────────────────────┐
│ Tactiq                                      LIVE ●   │
├─────────────────────────────────────────────────────────┤
│                                                         │
│        TEAM A       1  -  1       TEAM B               │
│                         67:42                           │
│                                                         │
│  ┌───────────────────────────┐ ┌─────────────────────┐ │
│  │                           │ │ 🔥 PRESSURE BUILDING │ │
│  │       MATCH PITCH         │ │                     │ │
│  │                           │ │ Team A are beginning │ │
│  │       ●──────●            │ │ to pin Team B back. │ │
│  │            ⚽             │ │                     │ │
│  │    ●          ●           │ │ 6 progressive      │ │
│  │                           │ │ passes              │ │
│  │                           │ │ 4 final-third       │ │
│  │                           │ │ entries             │ │
│  └───────────────────────────┘ └─────────────────────┘ │
│                                                         │
│  MATCH RHYTHM       ████████████░░                     │
│                                                         │
│  [ Fan ] [ Analyst ] [ Player ]                        │
│                                                         │
│  Team: [ Team A ▼ ]   Player: [ Player X ▼ ]           │
│  Language: [ English ▼ ]                                │
│  Output: [ Text ▼ ]                                    │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ EVENT TIMELINE                                         │
│ 67:42  Pressure increased                              │
│ 67:10  Final-third entry                               │
│ 66:51  Progressive pass                                │
└─────────────────────────────────────────────────────────┘
```

------------------------------------------------------------------------

# 15. Match visualization

A full broadcast-quality video system is unnecessary for the initial
project.

Instead, Tactiq can use a synthetic match visualization.

The visualization can show:

-   pitch;
-   players;
-   ball;
-   player movement;
-   passes;
-   shots;
-   possession;
-   event markers;
-   team formations;
-   current match time.

The visualizer provides the context in which the AI insight is
displayed.

This makes the demo self-contained.

------------------------------------------------------------------------

# 16. User personalization

The personalization layer should not simply change colors or UI
preferences.

It should change **what information is emphasized**.

------------------------------------------------------------------------

## 16.1 Casual fan

Focus:

-   simple language;
-   major moments;
-   short explanations;
-   momentum;
-   goals;
-   exciting sequences.

Example:

> **Team A are turning the pressure up. They've had four attacks in the
> last minute and just forced a save.**

------------------------------------------------------------------------

## 16.2 Analyst

Focus:

-   detailed metrics;
-   tactical context;
-   event sequences;
-   player roles;
-   pressure;
-   territory.

Example:

> **Team A's final-third occupation has increased over the last 90
> seconds, with six progressive passes and four entries from the left
> channel.**

------------------------------------------------------------------------

## 16.3 Player-focused mode

The user selects a player.

The system tracks:

-   touches;
-   passes;
-   progressive actions;
-   shots;
-   defensive actions;
-   involvement;
-   successful actions;
-   recent changes in activity.

Example:

> **Player X has been involved in five of Team A's last seven
> progressive actions.**

------------------------------------------------------------------------

## 16.4 Metric-focused mode

The viewer selects something such as:

-   passing;
-   possession;
-   pressure;
-   shots;
-   player involvement;
-   speed;
-   distance.

The system prioritizes insights related to that metric.

------------------------------------------------------------------------

# 17. Broadcast-oriented output

Although Tactiq is primarily an interactive web experience, the
intelligence layer should produce structured outputs that could
theoretically be consumed by a broadcast graphics system.

Example:

``` json
{
  "timestamp": "00:67:42",
  "priority": "HIGH",
  "type": "TACTICAL_INSIGHT",
  "title": "Pressure Building",
  "summary": "Team A are beginning to pin Team B back.",
  "duration_seconds": 8,
  "confidence": 0.91,
  "team": "team_a",
  "evidence": {
    "progressive_passes": 6,
    "final_third_entries": 4,
    "shots": 1
  }
}
```

This makes the output machine-readable.

A separate renderer could then decide how to display it.

------------------------------------------------------------------------

# 18. Automated match recap

The same intelligence generated during the match can be used after the
match.

Instead of analyzing the match from scratch, Tactiq can store
significant insights throughout the game.

At full time:

``` text
Match
 ↓
Significant insights collected
 ↓
Ranked moments
 ↓
Narrative synthesis
 ↓
Personalized recap
```

Possible recap:

### General recap

> Team A started slowly but increased territorial pressure after
> halftime. Their strongest period came between minutes 62 and 71, when
> repeated final-third entries produced three shots.

### Player recap

> Player X became increasingly involved after halftime and contributed
> to seven progressive actions during Team A's strongest attacking
> period.

### Analyst recap

A more detailed sequence of tactical and statistical developments.

### Multilingual recap

The same recap localized to the viewer's preferred language.

------------------------------------------------------------------------

# 19. Technology stack

The exact stack can evolve during implementation, but the initial plan
is:

## 19.1 Cloud and AI

### Microsoft Azure

Primary cloud platform.

Planned uses:

-   application hosting;
-   AI services;
-   storage;
-   event processing;
-   databases;
-   monitoring;
-   potentially speech and translation.

The project should make meaningful use of Azure rather than merely
deploying a static frontend.

------------------------------------------------------------------------

## 19.2 Microsoft Foundry

Microsoft Foundry will be the primary AI/agent development platform.

Potential responsibilities:

-   model access;
-   agent development;
-   agent orchestration;
-   tools;
-   evaluation;
-   observability;
-   AI application development.

The exact Foundry capabilities selected will depend on the
implementation available during the build.

------------------------------------------------------------------------

## 19.3 Agent framework

A Microsoft-supported agent framework can be used where appropriate to
define:

-   agent roles;
-   tools;
-   handoffs;
-   shared state;
-   orchestration;
-   structured outputs.

The goal is not to use a framework for its own sake.

The framework should make the multi-agent workflow clearer and more
maintainable.

------------------------------------------------------------------------

## 19.4 Backend

### Python

Python is a strong candidate for:

-   synthetic match simulation;
-   event processing;
-   football metrics;
-   pattern detection;
-   agent orchestration;
-   APIs.

Potential framework:

-   FastAPI.

------------------------------------------------------------------------

## 19.5 Frontend

### TypeScript

TypeScript is a strong candidate for the frontend.

Potential stack:

-   React;
-   Next.js;
-   Tailwind CSS;
-   browser-based event streaming;
-   interactive match visualization.

The frontend should consume structured insight data from the backend.

------------------------------------------------------------------------

## 19.6 Data layer

Potential Azure data services:

-   Azure Cosmos DB;
-   Azure Database for PostgreSQL;
-   Azure Blob Storage;
-   or another appropriate Azure database.

The final choice should depend on the event model and implementation
complexity.

The system does not need a large database for the hackathon.

------------------------------------------------------------------------

## 19.7 Event streaming

Possible implementation:

-   Azure Event Hubs;
-   WebSockets;
-   Server-Sent Events;
-   or a lightweight application-level event stream.

The important behavior is:

``` text
event occurs
     ↓
backend receives event
     ↓
state updates
     ↓
insight detection
     ↓
UI receives update
```

A local simulator can initially feed the backend.

Azure event infrastructure can be added where it meaningfully
demonstrates cloud-native architecture.

------------------------------------------------------------------------

## 19.8 Translation and speech

Potential Azure services:

-   Azure AI Translator;
-   Azure Speech.

Potential flow:

``` text
Structured Insight
       ↓
Localization
       ↓
Translated narrative
       ↓
Speech synthesis
```

Speech should remain an optional extension until the core intelligence
is stable.

------------------------------------------------------------------------

## 19.9 Development tools

Potential development tools include:

-   GitHub;
-   GitHub Copilot;
-   GitHub CLI;
-   VS Code;
-   Azure CLI;
-   Microsoft Foundry;
-   Azure Portal.

------------------------------------------------------------------------

# 20. Proposed repository structure

``` text
tactiq/
│
├── README.md
├── PROJECT.md
├── LICENSE
├── .env.example
├── .gitignore
│
├── apps/
│   └── web/
│       ├── app/
│       ├── components/
│       ├── lib/
│       └── public/
│
├── services/
│   ├── api/
│   │   ├── routes/
│   │   ├── schemas/
│   │   └── main.py
│   │
│   ├── simulator/
│   │   ├── scenarios/
│   │   ├── players/
│   │   ├── teams/
│   │   ├── event_generator.py
│   │   └── match_engine.py
│   │
│   ├── intelligence/
│   │   ├── metrics/
│   │   ├── patterns/
│   │   ├── match_state/
│   │   └── insight_detection/
│   │
│   └── agents/
│       ├── event_analyst/
│       ├── tactical_analyst/
│       ├── narrative/
│       ├── personalization/
│       ├── localization/
│       └── commentary/
│
├── data/
│   ├── schemas/
│   ├── scenarios/
│   └── examples/
│
├── infrastructure/
│   ├── azure/
│   └── deployment/
│
├── tests/
│   ├── simulator/
│   ├── intelligence/
│   ├── agents/
│   └── integration/
│
├── docs/
│   ├── architecture.md
│   ├── data-model.md
│   ├── agent-design.md
│   ├── evaluation.md
│   └── demo-script.md
│
└── scripts/
    ├── seed_data.py
    ├── run_match.py
    └── local_dev.py
```

This is a proposed structure, not a requirement. The final repository
should remain as simple as possible while preserving clean separation of
responsibilities.

------------------------------------------------------------------------

# 21. End-to-end workflow

The complete workflow can be described as follows.

## Step 1 --- Start a synthetic match

The user selects:

-   Team A;
-   Team B;
-   match scenario;
-   optional simulation speed.

The match begins.

------------------------------------------------------------------------

## Step 2 --- Generate events

The simulator generates events:

``` text
pass
pass
possession
pressure
pass
tackle
pass
shot
```

Each event receives a timestamp.

------------------------------------------------------------------------

## Step 3 --- Update football state

The intelligence engine updates:

-   possession;
-   territory;
-   passing patterns;
-   pressure;
-   player involvement;
-   match rhythm;
-   recent sequences.

------------------------------------------------------------------------

## Step 4 --- Detect patterns

The insight detector asks:

> Has anything meaningful changed?

For example:

``` text
6 progressive passes
+
4 final-third entries
+
1 shot
+
high possession retention
=
potential sustained pressure
```

------------------------------------------------------------------------

## Step 5 --- Build evidence

The system collects the relevant evidence.

``` text
Insight:
Pressure Building

Evidence:
6 progressive passes
4 final-third entries
71 seconds of sustained possession
1 shot
```

------------------------------------------------------------------------

## Step 6 --- Tactical interpretation

The Tactical Intelligence Agent receives the verified state and
determines what the pattern means.

------------------------------------------------------------------------

## Step 7 --- Narrative generation

The Narrative Agent converts the interpretation into human-readable
language.

------------------------------------------------------------------------

## Step 8 --- Personalization

The Personalization Agent adapts the content based on:

-   audience;
-   selected team;
-   selected player;
-   selected metric;
-   verbosity.

------------------------------------------------------------------------

## Step 9 --- Localization

The Localization layer produces the requested language.

------------------------------------------------------------------------

## Step 10 --- Rendering

The frontend receives a structured insight and displays it.

------------------------------------------------------------------------

## Step 11 --- Optional speech

If enabled:

``` text
localized narrative
       ↓
speech synthesis
       ↓
spoken commentary
```

------------------------------------------------------------------------

## Step 12 --- Store significant insights

Important moments are retained.

At full time they become the basis for the match recap.

------------------------------------------------------------------------

# 22. Example end-to-end scenario

Consider a fictional synthetic match:

``` text
Team A vs Team B
Minute: 67:42
Score: 1–1
```

The event stream produces:

``` text
66:51  Team A progressive pass
67:02  Team A successful pass
67:10  Team A final-third entry
67:19  Team A progressive pass
67:27  Team A pressure event
67:34  Team A successful pass
67:38  Team A final-third entry
67:42  Team A shot
```

The intelligence engine calculates:

``` text
6 progressive/successful attacking actions
4 final-third entries
1 shot
increased territorial control
```

The pattern detector identifies:

> **Sustained attacking pressure**

The Tactical Agent explains:

> Team A are maintaining territorial pressure through repeated
> progressive actions and final-third entries.

The Narrative Agent produces:

> **Team A are starting to pin Team B back.**

The Personalization Agent receives:

``` text
audience = analyst
```

and expands it:

> Team A have increased territorial control over the last 90 seconds,
> with repeated progressive actions and four final-third entries
> culminating in a shot.

The viewer switches language:

``` text
language = Kiswahili
```

The localization layer produces a Kiswahili version.

The viewer switches to:

``` text
mode = player-focused
player = Player X
```

The system now prioritizes Player X's role in the sequence.

One underlying event stream has therefore produced multiple experiences.

------------------------------------------------------------------------

# 23. Multi-agent collaboration model

The multi-agent system should have purposeful collaboration.

A proposed handoff chain:

``` text
Event Stream
     │
     ▼
Event Analyst
     │
     │ verified observation
     ▼
Tactical Analyst
     │
     │ tactical interpretation
     ▼
Narrative Agent
     │
     │ narrative concept
     ▼
Personalization Agent
     │
     │ audience-specific representation
     ▼
Localization Agent
     │
     │ requested language
     ▼
Commentary / Renderer
```

Each stage should have a defined input and output.

This avoids creating an artificial architecture where multiple agents
perform essentially the same task.

------------------------------------------------------------------------

# 24. Shared state

Agents should not operate as completely isolated chat sessions.

A shared match state can contain:

``` json
{
  "match_id": "match_001",
  "clock": "67:42",
  "score": {
    "team_a": 1,
    "team_b": 1
  },
  "possession": {
    "team_a": 0.61,
    "team_b": 0.39
  },
  "active_insight": {
    "type": "pressure_shift",
    "team": "team_a"
  },
  "selected_team": "team_a",
  "selected_player": "player_17",
  "audience": "casual",
  "language": "en"
}
```

This allows agents to reason from a common state.

------------------------------------------------------------------------

# 25. Failure handling

The system should degrade gracefully.

### If the Narrative Agent fails

Display the deterministic insight.

### If localization fails

Fall back to the default language.

### If commentary fails

Keep the text insight.

### If an insight has low confidence

Do not display it as a strong claim.

### If the event stream pauses

The UI should indicate that the feed is delayed rather than inventing
new events.

This is particularly important because a live intelligence product
should prefer an explicit fallback to fabricated information.

------------------------------------------------------------------------

# 26. Confidence and evidence

Every AI-generated insight should ideally be associated with:

-   source event window;
-   calculated metrics;
-   confidence;
-   insight type;
-   timestamp;
-   affected team;
-   affected player where relevant.

Example:

``` json
{
  "insight_id": "ins_102",
  "timestamp": "67:42",
  "type": "pressure_shift",
  "confidence": 0.91,
  "team": "team_a",
  "evidence_window": {
    "start": "66:30",
    "end": "67:42"
  },
  "evidence": {
    "progressive_passes": 6,
    "final_third_entries": 4,
    "shots": 1
  }
}
```

This supports explainability and debugging.

------------------------------------------------------------------------

# 27. Performance considerations

Because the project is intended for live scenarios, latency matters.

The system should avoid calling a large language model for every event.

Instead:

``` text
Every event
     ↓
Cheap deterministic processing
     ↓
Pattern detection
     ↓
Only meaningful patterns trigger AI
```

This reduces:

-   latency;
-   token consumption;
-   unnecessary model calls;
-   duplicate insights.

It also makes the architecture more realistic for production.

------------------------------------------------------------------------

# 28. Cost-conscious architecture

The project can take advantage of the available Azure environment, but
cloud resources should still be used deliberately.

Potential strategy:

### High-frequency operations

Run locally or through lightweight backend logic:

-   event ingestion;
-   counters;
-   possession;
-   rolling windows;
-   thresholds;
-   deterministic metrics.

### Lower-frequency AI operations

Use Foundry/model calls for:

-   tactical interpretation;
-   narrative generation;
-   personalization;
-   localization where necessary;
-   recap synthesis.

### Optional expensive operations

Use only when requested:

-   voice generation;
-   advanced reasoning;
-   long-form match analysis.

This creates a more efficient architecture than sending the entire match
stream to an LLM.

------------------------------------------------------------------------

# 29. Data model

The system can use several core entities.

## Match

``` text
match_id
team_a
team_b
start_time
current_time
score
status
```

## Team

``` text
team_id
name
formation
players
```

## Player

``` text
player_id
name
position
team_id
attributes
```

## Event

``` text
event_id
match_id
timestamp
event_type
team_id
player_id
location
metadata
```

## Insight

``` text
insight_id
match_id
timestamp
type
priority
confidence
summary
evidence
```

## User preferences

``` text
audience_mode
favorite_team
favorite_player
language
preferred_metric
output_type
verbosity
```

------------------------------------------------------------------------

# 30. API concept

A possible backend API:

``` text
POST /matches
GET  /matches/{id}
POST /matches/{id}/start
POST /matches/{id}/pause

GET  /matches/{id}/events
GET  /matches/{id}/state
GET  /matches/{id}/insights

POST /matches/{id}/preferences
GET  /matches/{id}/recap

GET  /stream/{id}
```

The final API design may change during implementation.

------------------------------------------------------------------------

# 31. Evaluation strategy

Tactiq should not only be evaluated by whether the application works.

It should be evaluated at multiple layers.

## 31.1 Simulator evaluation

Does the simulator produce plausible event sequences?

## 31.2 Metric evaluation

Are deterministic calculations correct?

## 31.3 Pattern evaluation

Does the system correctly detect predefined scenarios?

## 31.4 Agent evaluation

Does the agent correctly interpret the verified state?

## 31.5 Grounding evaluation

Does the narrative remain consistent with the evidence?

## 31.6 Personalization evaluation

Does the same intelligence meaningfully change between:

-   fan;
-   analyst;
-   player-focused;
-   metric-focused modes?

## 31.7 Localization evaluation

Does the translated narrative preserve the original football meaning?

## 31.8 Latency evaluation

How quickly does an event become a visible insight?

------------------------------------------------------------------------

# 32. Example test

Given:

``` text
6 progressive passes
4 final-third entries
1 shot
71 seconds
```

The system should be capable of detecting:

``` text
pressure_increasing = true
```

It should not claim:

``` text
Team A dominated the entire match.
```

because the evidence only covers a specific time window.

This distinction is important.

Tactiq should reason from available evidence rather than exaggerating
the meaning of a short sequence.

------------------------------------------------------------------------

# 33. Responsible AI considerations

The system should be designed so that AI-generated commentary remains
grounded in the synthetic match state.

Important principles:

### No invented statistics

If a metric was not calculated, the model should not invent it.

### No unsupported player claims

The system should only make player-performance claims supported by the
event stream.

### Confidence-aware insights

Low-confidence observations should be treated differently from strong
signals.

### Clear synthetic-data framing

The demo should make it clear that the match is synthetic.

### No unauthorized Premier League content

The project should not redistribute copyrighted Premier League footage
or proprietary match data without the necessary rights.

------------------------------------------------------------------------

# 34. Why synthetic data is more than a compliance requirement

The synthetic dataset can become one of the project's strongest
engineering components.

Instead of creating random football JSON, Tactiq can create a
reusable football simulation environment.

The dataset can contain:

-   normal matches;
-   high-pressure periods;
-   low-event periods;
-   tactical changes;
-   counterattacks;
-   player-dominance scenarios;
-   unusual events;
-   momentum shifts;
-   different formations;
-   different match states.

This makes the simulator useful for:

-   development;
-   testing;
-   agent evaluation;
-   demos;
-   regression tests;
-   future research.

------------------------------------------------------------------------

# 35. Potential future extensions

The following features are intentionally outside the initial MVP but can
be added later.

## 35.1 Video-based auto-eventing

Instead of receiving structured events from the simulator:

``` text
Video
 ↓
Computer vision
 ↓
Detected events
 ↓
Football Intelligence
```

This would approach the challenge's optional auto-eventing direction.

------------------------------------------------------------------------

## 35.2 Voice interaction

A viewer could ask:

> "Why is Team A suddenly attacking more?"

The system could answer using the current match state.

------------------------------------------------------------------------

## 35.3 Conversational match assistant

A viewer could ask:

> "How has Player X performed since halftime?"

The system would query the event state and respond with evidence.

------------------------------------------------------------------------

## 35.4 Personalized notifications

A user interested in Player X could receive:

> Player X has just reached 10 progressive actions.

------------------------------------------------------------------------

## 35.5 Automatic highlight identification

Instead of identifying highlights only by goals, the system could
identify:

-   tactical turning points;
-   momentum shifts;
-   high-pressure sequences;
-   player breakthroughs;
-   unusual statistical events.

------------------------------------------------------------------------

## 35.6 More languages

The language layer could eventually support a broad set of languages
depending on available translation and speech services.

------------------------------------------------------------------------

# 36. MVP definition

The project should not attempt to implement everything.

The minimum compelling version should include:

### Required

-   synthetic match simulator;
-   live event stream;
-   football intelligence engine;
-   meaningful pattern detection;
-   multi-agent reasoning;
-   explainable insights;
-   web interface;
-   at least two audience modes;
-   language selection;
-   structured insight output.

### Strong extensions

-   Kiswahili support;
-   spoken commentary;
-   player-focused mode;
-   match recap;
-   event timeline;
-   Azure event infrastructure;
-   confidence visualization.

### Optional stretch

-   video auto-eventing;
-   conversational match assistant;
-   voice interaction;
-   automatic highlight generation.

------------------------------------------------------------------------

# 37. Proposed MVP user journey

A judge opens Tactiq.

### Step 1

They start a synthetic match.

### Step 2

The pitch visualization begins.

### Step 3

Events appear in real time.

### Step 4

The system detects a meaningful pattern.

### Step 5

An insight appears:

> **Pressure Building**

### Step 6

The judge clicks:

> **Why does this matter?**

The system reveals the supporting evidence.

### Step 7

The judge switches:

> **Fan → Analyst**

The explanation becomes more technical.

### Step 8

The judge selects:

> **Player X**

The intelligence becomes player-focused.

### Step 9

The judge switches:

> **English → Kiswahili**

The same insight is delivered in the selected language.

### Step 10

Optional:

> **Text → Voice**

The insight becomes spoken commentary.

This demonstrates the entire concept in a short interaction.

------------------------------------------------------------------------

# 38. Demonstration philosophy

The final demo should not spend most of its time explaining
architecture.

The product should demonstrate itself.

The ideal sequence is:

``` text
MATCH STARTS
     ↓
EVENTS ARRIVE
     ↓
PATTERN EMERGES
     ↓
AI EXPLAINS IT
     ↓
VIEWER CHANGES MODE
     ↓
SAME INTELLIGENCE CHANGES
     ↓
VIEWER CHANGES LANGUAGE
     ↓
COMMENTARY CHANGES
```

The viewer should be able to see that the system is not simply
generating generic football text.

It is responding to the same underlying match state.

------------------------------------------------------------------------

# 39. What makes the project different

The differentiation should come from the combination of several
capabilities rather than one isolated feature.

### 39.1 It is event-driven

The system reacts to a live synthetic event stream.

### 39.2 It separates facts from generation

Football metrics are calculated before language generation.

### 39.3 It understands sequences

The system looks for relationships between events rather than treating
each event independently.

### 39.4 It explains significance

The system attempts to answer why a moment matters.

### 39.5 It is agentic

Specialized agents collaborate across analysis, interpretation,
narrative, personalization, and localization.

### 39.6 It is personalized

Different viewers can receive genuinely different representations of the
same football intelligence.

### 39.7 It is multilingual

The same match intelligence can be delivered in the viewer's preferred
language.

### 39.8 It is designed for live experiences

Insights are timestamped and structured so they can be rendered
alongside a match.

------------------------------------------------------------------------

# 40. Alignment with the hackathon

The official hackathon challenge describes a pipeline of:

1.  **Ingest** synthetic match events.
2.  **Interpret** them into meaningful football statistics, patterns,
    and context.
3.  **Explain** why a moment matters.
4.  **Render** the insight alongside the match.
5.  **Personalize** the intelligence for different audiences.

Tactiq is designed directly around those five stages.

The official challenge also identifies real-time match intelligence,
narrative generation, explainability, synthetic football-realistic
datasets, and multi-language storytelling as areas projects may explore.

The project therefore treats these capabilities as first-class design
components rather than unrelated add-ons.

------------------------------------------------------------------------

# 41. Alignment with judging considerations

The official rules state that submissions that pass the baseline
viability stage are evaluated across five equally weighted areas:

-   Technological Implementation;
-   Agentic Design & Innovation;
-   Real-World Impact & Applicability;
-   UX & Presentation;
-   Adherence to Category.

Tactiq's architecture is therefore designed to demonstrate evidence
for each area.

### Technological Implementation

Demonstrate:

-   Azure;
-   Microsoft Foundry;
-   structured software architecture;
-   synthetic data generation;
-   event processing;
-   AI services;
-   maintainable code;
-   cloud deployment.

### Agentic Design & Innovation

Demonstrate:

-   specialized agent roles;
-   orchestration;
-   handoffs;
-   shared match state;
-   grounded reasoning;
-   personalization;
-   localization.

### Real-World Impact & Applicability

Demonstrate possible use across:

-   broadcasters;
-   streaming platforms;
-   football studios;
-   analysts;
-   clubs;
-   fans;
-   multilingual audiences.

### UX & Presentation

Demonstrate:

-   real-time visual experience;
-   understandable insights;
-   evidence;
-   personalization;
-   language switching;
-   clear interaction.

### Adherence to Category

Demonstrate:

-   synthetic data;
-   football-realistic events;
-   match intelligence;
-   narratives;
-   explanation;
-   rendering;
-   personalization.

------------------------------------------------------------------------

# 42. Competition compliance considerations

The project must remain compliant with the official rules.

## New project

The rules require entrants to build a new solution responding to the
challenge.

Tactiq should therefore be developed as a distinct project rather
than retrofitting an existing application.

Existing engineering knowledge and general technical experience can
inform the implementation, but the submitted project itself should be
new.

## Synthetic data

The project should use synthetic football-realistic data.

## Intellectual property

The project should only include content and components for which the
team has appropriate rights.

## Public GitHub repository

The submission requires a public GitHub repository.

## Demo video

The submission requires a short demo video, with the official rules
specifying a video of less than two minutes.

The final implementation should therefore be designed so the core value
can be demonstrated quickly.

------------------------------------------------------------------------

# 43. Development phases

## Phase 0 --- Design

Before implementation:

-   finalize architecture;
-   define event schema;
-   define match state;
-   define insight schema;
-   define agent responsibilities;
-   define MVP;
-   define synthetic scenarios.

------------------------------------------------------------------------

## Phase 1 --- Synthetic match engine

Build:

-   teams;
-   players;
-   event generator;
-   match clock;
-   scenarios;
-   event stream.

Goal:

> Generate a believable stream of football events.

------------------------------------------------------------------------

## Phase 2 --- Football intelligence

Build:

-   possession;
-   pass metrics;
-   progression;
-   territory;
-   pressure;
-   player involvement;
-   rolling windows;
-   pattern detection.

Goal:

> Convert raw events into reliable football state.

------------------------------------------------------------------------

## Phase 3 --- Insight engine

Build:

-   insight types;
-   triggers;
-   evidence;
-   confidence;
-   priority;
-   timestamps.

Goal:

> Determine when something meaningful has happened.

------------------------------------------------------------------------

## Phase 4 --- Agents

Implement:

-   Event Analyst;
-   Tactical Intelligence;
-   Narrative;
-   Personalization.

Goal:

> Convert verified state into meaningful explanations.

------------------------------------------------------------------------

## Phase 5 --- Web UI

Build:

-   match visualization;
-   event timeline;
-   live insights;
-   evidence panel;
-   audience modes;
-   player selection.

Goal:

> Make the intelligence visible.

------------------------------------------------------------------------

## Phase 6 --- Multilingual layer

Add:

-   language preference;
-   localization;
-   Kiswahili;
-   additional languages where practical.

Goal:

> Make the same intelligence accessible across language contexts.

------------------------------------------------------------------------

## Phase 7 --- Commentary

Add:

-   text commentary;
-   optional speech;
-   commentary timing;
-   commentary controls.

Goal:

> Turn insights into a broadcast-like experience.

------------------------------------------------------------------------

## Phase 8 --- Cloud deployment

Deploy appropriate components to Azure.

Goal:

> Demonstrate a real cloud-native AI system.

------------------------------------------------------------------------

## Phase 9 --- Evaluation and hardening

Test:

-   correctness;
-   latency;
-   grounding;
-   agent handoffs;
-   failure handling;
-   multilingual consistency;
-   UI behavior.

------------------------------------------------------------------------

## Phase 10 --- Demo preparation

Create:

-   polished scenario;
-   short demo flow;
-   architecture diagram;
-   README;
-   project documentation;
-   public repository;
-   demo video.

------------------------------------------------------------------------

# 44. Key design principles

## Principle 1 --- Facts before language

Calculate first.

Generate language second.

------------------------------------------------------------------------

## Principle 2 --- Events are not insights

A pass is an event.

A sequence of passes creating sustained pressure can become an insight.

------------------------------------------------------------------------

## Principle 3 --- Not every insight needs an LLM

Use deterministic logic wherever possible.

Use AI where interpretation or natural-language generation adds value.

------------------------------------------------------------------------

## Principle 4 --- Agents need distinct responsibilities

Do not create agents simply to increase the number of agents.

Each agent should solve a specific part of the problem.

------------------------------------------------------------------------

## Principle 5 --- Personalization must change the experience

Changing a label from "Fan" to "Analyst" is not personalization.

The content, detail, metrics, and narrative emphasis should actually
change.

------------------------------------------------------------------------

## Principle 6 --- Language should be separated from football reasoning

The system should reason about football once and then express that
intelligence in different languages.

------------------------------------------------------------------------

## Principle 7 --- Evidence should follow every important claim

An insight should be traceable to a time window and supporting metrics.

------------------------------------------------------------------------

## Principle 8 --- Live systems should fail safely

When the AI cannot confidently explain something, the system should fall
back to verified information rather than inventing an answer.

------------------------------------------------------------------------

## Principle 9 --- The simulator should be reusable

Synthetic data generation should support development, testing,
evaluation, and demonstration.

------------------------------------------------------------------------

## Principle 10 --- Build the smallest compelling system first

A reliable core experience is more valuable than a large collection of
unfinished features.

------------------------------------------------------------------------

# 45. Core success criteria

The project should be considered technically successful when it can
demonstrate all of the following:

-   A synthetic match can run continuously.
-   Events are generated in real time.
-   Events are processed into football metrics.
-   Meaningful patterns can be detected.
-   Insights contain evidence.
-   Agents interpret the detected patterns.
-   The UI displays insights in real time.
-   Different viewer modes produce different explanations.
-   A selected player can become the focus of the experience.
-   The same intelligence can be delivered in multiple languages.
-   The architecture can optionally produce spoken commentary.
-   A match recap can be generated from stored insights.
-   The project can run using Azure services and Microsoft AI
    technologies.
-   The system can be demonstrated without relying on copyrighted
    Premier League footage or proprietary match data.

------------------------------------------------------------------------

# 46. The core product statement

Tactiq can ultimately be described in one sentence:

> **Tactiq is an agentic AI football intelligence engine that turns
> synthetic live match events into explainable, personalized and
> multilingual match stories.**

Or, more simply:

> **It understands what happened, why it matters, and how each viewer
> wants to experience it.**

------------------------------------------------------------------------

# 47. Project vision

The long-term vision is not simply to build an AI football commentator.

It is to build a general-purpose **match intelligence layer**.

The underlying architecture could eventually sit between football data
and multiple experiences:

``` text
                 FOOTBALL DATA
                       │
                       ▼
             MATCH INTELLIGENCE
                       │
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
   Broadcast        Streaming         Fans
       │               │                │
       ▼               ▼                ▼
   Overlays        Commentary       Personalization
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                  MATCH STORY
```

The core idea is therefore broader than statistics and broader than
commentary.

It is:

> **Turning football data into understanding.**

------------------------------------------------------------------------

# 48. Final architecture summary

``` text
┌──────────────────────────────────────────────────────────────┐
│                    SYNTHETIC MATCH ENGINE                    │
│                                                              │
│ Teams • Players • Formations • Events • Match Scenarios     │
└───────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│                       EVENT STREAM                            │
│                                                              │
│ Pass • Shot • Tackle • Possession • Pressure • Movement     │
└───────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│                  FOOTBALL INTELLIGENCE ENGINE                 │
│                                                              │
│ Metrics • Match State • Rolling Windows • Pattern Detection  │
└───────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│                     INSIGHT DETECTOR                         │
│                                                              │
│ What changed? • Is it meaningful? • What evidence supports? │
└───────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│                       AGENTIC LAYER                           │
│                                                              │
│ Event Analyst → Tactical → Narrative → Personalization      │
│                                      ↓                       │
│                               Localization                   │
│                                      ↓                       │
│                                Commentary                    │
└───────────────────────────────┬──────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────┐
│                       EXPERIENCE LAYER                        │
│                                                              │
│ Live Match UI • Insights • Evidence • Fan Mode • Analyst    │
│ Mode • Player Mode • Language • Text • Voice • Recap        │
└──────────────────────────────────────────────────────────────┘
```

------------------------------------------------------------------------

# 49. One-line mental model for implementation

When building Tactiq, keep this mental model:

``` text
EVENTS
  ↓
FACTS
  ↓
PATTERNS
  ↓
MEANING
  ↓
STORY
  ↓
PERSONALIZATION
  ↓
LANGUAGE / VOICE
  ↓
VIEWER
```

The project succeeds when each layer has a clear responsibility.

The AI is not there to replace the football data pipeline.

The football data pipeline gives the AI something trustworthy to
understand.

The agents are not there merely to generate text.

They transform verified match state into context, explanation,
narrative, personalization, and communication.

The interface is not the intelligence.

It is the surface through which the intelligence becomes useful.

------------------------------------------------------------------------

# 50. Current implementation priority

Before writing production code, finalize these five artifacts:

1.  **Event schema** --- exactly what a synthetic football event
    contains.
2.  **Match state model** --- exactly what the intelligence engine knows
    at any point in the match.
3.  **Insight schema** --- exactly what constitutes a meaningful insight
    and how evidence is attached.
4.  **Agent contracts** --- exactly what each agent receives and
    returns.
5.  **MVP interaction flow** --- exactly what a judge can do during the
    live demonstration.

Once those five are stable, implementation can proceed from the
simulator upward.

**The first thing to build should therefore not be the UI or the agents.
It should be the synthetic match/event engine and the football state
model.**

That becomes the foundation for everything else.
