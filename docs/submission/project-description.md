# Tactiq
## See the play. Understand the game.

[Watch the 1:55 pitch](https://youtu.be/ktO6Mbmkhy4) · [Explore the public code](https://github.com/wenakanew/tactiq) · [Try the Azure pilot](https://ca-tactiq-web-3e4d.salmongrass-337969b4.westus3.azurecontainerapps.io/workspace) · [Free local judge testing](demo-guide.md#free-local-judge-testing)

### Football shows us what happened. Tactiq helps us understand why it mattered.

Tactiq is an evidence-first football intelligence workspace built around the Microsoft Premier League Hackathon's Inside the Game challenge: **transform synthetic football events into meaningful statistics, explainable moments, narratives and personalized experiences.**

It combines a reproducible analytical pipeline with Microsoft Foundry, Microsoft Agent Framework and Azure AI Speech. Fans get accessible explanations, analysts get supporting measurements, and player-focused viewers get attributable context where the evidence supports it.

**Not just AI talking about football. Football intelligence that shows its work.**

## The problem

A scoreline tells us the outcome, not the story behind it.

The build-up of pressure, a change in rhythm or a rapid transition can explain more than an isolated highlight. Yet understanding those passages often requires specialist knowledge and switching between footage, statistics and analysis tools.

Tactiq brings the moment, its supporting evidence and an audience-appropriate explanation into one browser experience. Its potential use is a companion intelligence layer for studio, broadcast or streaming workflows, not a claim of certified broadcast integration.

## What we built

The workspace connects **The match**, **Moments** and **The story**:

- Explore structured match state and available observations.
- Discover pressure, rhythm, influence and transition insights tied to evidence windows.
- Play, pause and seek synchronized pitch replay.
- Inspect a moment's measurements before reading its explanation.
- Change between Fan, Analyst and evidence-supported Player perspectives.
- Choose from seven text locales and generate a recap.
- Use optional grounded lead/analyst commentary and Azure dual-voice speech.

The submission demonstration uses **our own labelled, football-realistic synthetic scenarios**. No external match footage is required to reproduce the core experience.

## One pipeline, all five challenge stages

| Stage | What Tactiq does | Why it matters |
| --- | --- | --- |
| **Ingest** | Produces versioned, seeded synthetic events, timestamps, metadata and player/ball trajectories. | Reproducible inputs let judges inspect the same kinds of football passages without relying on unlicensed footage. |
| **Interpret** | Reduces events into match state, statistics, patterns and evidence-backed moments. | Meaning comes from the passage, not just an event count or scoreline. |
| **Explain** | Uses structured evidence for Foundry narratives and validated Agent Framework lead/analyst exchanges. | The explanation addresses why a moment matters while staying within observed context. |
| **Render** | Connects insights to the browser workspace, synchronized replay, recap chapters and machine-readable exports. | Viewers can move from a claim to its supporting moment. |
| **Personalize** | Changes depth, language and player focus while preserving the evidence base. | A casual fan and a data-hungry analyst receive genuinely different presentations of the same facts. |

The local reference processes synthetic scenarios into an interactive experience. The hosted pilot deliberately queues initial analysis and is not presented as a proven real-time broadcast service.

## Why evidence comes before explanation

A fluent answer is not necessarily a grounded answer.

Tactiq separates deterministic football analysis from generated language. The analytical core establishes measurements and evidence windows. AI explains that context rather than becoming the authority for match statistics.

Grounding checks constrain structured numbers, entities and output shape. They are useful safeguards, not a claim of perfect semantic verification. Provider failures produce disclosed deterministic fallbacks rather than being presented as successful model output.

Missing observations stay missing. Synthetic positions stay labelled. Uncalibrated pixels are not described as metres, and simulated speed fields are not marketed as validated real-video measurements.

**The audience perspective changes. The underlying facts do not.**

## Purposeful multi-agent collaboration

Tactiq's two commentary roles have distinct responsibilities:

1. **Lead:** describe the selected passage using an immutable evidence snapshot.
2. **Analyst:** add supported context after receiving the validated lead handoff.

Microsoft Agent Framework coordinates the ordered exchange. Both roles remain inside the same evidence boundary, and invalid output is rejected before assembly.

This separates observation from interpretation and makes the handoff inspectable. Actual provider evidence records both roles completing without fallback. The design demonstrates coordinated roles, not a measured claim that two agents always outperform one.

## Microsoft and Azure technologies

| Technology | Role in the project |
| --- | --- |
| **Microsoft Foundry** | Grounded narrative generation through the isolated `tactiq-commentary` model deployment under an existing Foundry project. |
| **Microsoft Agent Framework** | Ordered lead/analyst orchestration with shared evidence and validated handoffs. |
| **Azure AI Speech** | Ordered dual-voice spoken commentary. |
| **Azure Container Apps and Jobs** | Next.js frontend, internal FastAPI API and bounded background processing. |
| **Azure Service Bus** | Durable dispatch between the transactional outbox and analysis workers. |
| **Azure Blob Storage** | Private source media, generated audio and related artifacts. |
| **Azure Key Vault and managed identities** | Protected runtime configuration and scoped service access. |
| **Azure Virtual Machines, Virtual Network and Private DNS** | Private PostgreSQL 17 and private data-service connectivity. PostgreSQL runs on a VM, not Azure Database for PostgreSQL. |
| **Azure Container Registry** | Identified, immutable application images. |
| **Azure Monitor / Log Analytics** | Centralized operational logs. |
| **Microsoft identity platform / Microsoft Entra ID** | Certificate-backed Microsoft account integration; configuration and redirect are verified, but completed interactive sign-in remains unverified. |

The application uses Next.js/React, Python/FastAPI, PostgreSQL, FFmpeg and local ONNX tracking. Authorized video analysis is an optional extension, separate from the synthetic challenge demonstration.

## Why the architecture works this way

Analysis should not depend on keeping a web request open. Tactiq separates the interactive experience from durable cloud workers, using Service Bus, database-backed state and bounded execution.

Private media and workspace ownership help protect user material. Usage reservations occur before paid generation, completed results can be reused, and ambiguous calls are not automatically retried or treated as free.

The outcome is a connected, cost-aware hackathon pilot with a path toward broader adoption, not a claim of unlimited processing, enterprise certification or proven commercial impact.

## Grand Prize and category fit

Tactiq's overall case is **completeness across the five-stage challenge**, with explanation, rendering and personalization connected to the same evidence.

Its strongest category cases are:

- **Best Azure Cloud Native Integration:** a deployed event-driven application connecting Container Apps, Jobs, Service Bus, private data, managed identities and operational controls.
- **Best Use of Microsoft Foundry Project:** genuine grounded generation integrated into an inspectable football workflow, with actual output and usage receipts.
- **Best Multi-Agent System Prize:** distinct lead/analyst responsibilities, validated handoff and ordered output using Microsoft Agent Framework.

Private access, ownership checks, restore evidence and budget controls also demonstrate an enterprise adoption direction, while the remaining operational gaps stay explicit.

These are evidence-backed category fits, not award predictions. The rules permit at most one Grand Prize and one Category Prize; category selection and reassignment remain with the organizer.

## What has been demonstrated

The connected Azure pilot has completed a labelled synthetic analysis and returned evidence-linked results. Separately verified checks demonstrate genuine Foundry narrative generation, Agent Framework lead/analyst output, Azure dual-voice audio, private Blob full/range reads and workspace access checks. A private database backup was restored into an isolated database.

The pitch shows the real local browser application with synthetic data and disclosed deterministic explanations. Its Azure audio excerpt is previously verified provider output, not a new model call or proof of browser-authenticated playback.

Source, tests, exact deployment qualifications and sanitized provider receipts are linked in the repository.

## Judge access and honest limits

**Platform:** desktop web browser. **Core test route:** the public source and English local setup instructions provide repeatable synthetic analysis, evidence, replay, personalization and recaps without a Tactiq account, paid subscription or provider key.

The Azure endpoint is a supplemental budget-limited pilot. It has cold starts, up to fifteen-minute initial dispatch and one new native analysis per UTC day globally. It is not unrestricted hosted testing. Live Azure generation requires configured services and available shared budget; the local route uses deterministic explanations instead.

The entrant must maintain the offered testing route through the judging period and confirm any additional organizer-required access to paid AI. Production football-video accuracy, completed interactive Microsoft sign-in, sustained load, HA and enterprise readiness are not claimed as verified.

## The opportunity

Football is a shared experience. Understanding it should not depend on already being an expert.

Tactiq turns synthetic football events into something people can inspect, understand and experience from their own perspective. It pairs the reach of personalized storytelling with a simple principle:

**Every compelling explanation should have evidence behind it.**

### See the play. Understand the game.
