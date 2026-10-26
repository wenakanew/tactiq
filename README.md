# Tactiq

### Football intelligence. Evidence before explanation.

Football creates more events than a viewer can interpret. Tactiq connects passages of play to inspectable evidence, then makes that evidence understandable through personalized stories, synchronized replay, and optional spoken commentary.

**Ingest → Interpret → Explain → Render → Personalize**

[Explore the product](docs/product/user-guide.md) · [Architecture](docs/architecture/overview.md) · [Demo guide](docs/submission/demo-guide.md) · [Documentation](docs/README.md)

## Live Azure pilot

**[Open Tactiq](https://ca-tactiq-web-3e4d.salmongrass-337969b4.westus3.azurecontainerapps.io/workspace)** · [Deployment and production evidence](docs/azure/deployment.md)

The public UI is connected to the private API, PostgreSQL, private storage and durable cloud workers. A real synthetic scenario completed through this deployment. Cold starts can take around 90 seconds; initial job dispatch can take up to 15 minutes. The budget-sized pilot admits **one new analysis per UTC day globally**; reopening an owned analysis does not consume another admission.

![Real synthetic analysis completed in the public Azure workspace](docs/assets/production/scenario-analysis-live.png)

[Watch the live Azure workspace recording](docs/assets/production/workspace-live.webm) · [Listen to Azure dual-voice commentary](docs/assets/production/commentary-live.mp3) · [Inspect genuine Foundry output](docs/assets/production/foundry-commentary.json)

The [release record](docs/azure/deployment.md) distinguishes verified behavior from configured integrations, provider fallbacks and remaining qualifications. Screenshots are real production captures of labelled synthetic data, not football-accuracy claims.

## One match, different perspectives

| Perspective | What Tactiq brings into focus |
| --- | --- |
| **Fan** | A clear account of the important moment and why it matters |
| **Analyst** | Supporting windows, measurements, tactical context, and limitations |
| **Player** | Selected-player involvement where the evidence supports attribution |

Language and presentation change; the underlying facts do not.

## From a passage of play to an explanation

1. **Ingest** — Reproduce seeded synthetic scenarios or analyze an authorized video upload/link.
2. **Interpret** — Reduce events into football state, metrics, patterns, and evidence.
3. **Explain** — Build grounded narratives and lead/analyst commentary; disclose provider fallbacks.
4. **Render** — Explore moments, source clips, tracked trajectories, and synchronized replay.
5. **Personalize** — Adapt depth, player focus, language, and optional speech.

```mermaid
flowchart LR
    Input[Seeded scenarios / authorized video] --> Core[Deterministic state and intelligence]
    Core --> Evidence[Insights + evidence windows]
    Evidence --> Story[Grounded explanations]
    Evidence --> Replay[Source / pitch / split replay]
    Story --> Experience[Fan · Analyst · Player]
    Replay --> Experience
    Foundry[Optional Microsoft Foundry] -.-> Story
    Speech[Optional Azure Speech] -.-> Experience
```

## What is available

- Versioned synthetic scenes with player/ball trajectories and reproducible event streams.
- Local ONNX tracking, surveyed camera calibration, and explicit missing-observation states.
- Evidence-linked pressure, rhythm, influence, transition, and tactical insights.
- A workspace organized around **The match**, **Moments**, and **The story**.
- A shared video/replay clock, selectable moments, source clips, and calibrated or simulated pitch views.
- Seven text locales and optional ordered dual-voice commentary, with visible fallback status.
- A single-host reference runtime with tenant-scoped token access, durable SQLite jobs, and private local media.
- An opt-in deployed cloud runtime with private PostgreSQL/Blob, database-backed guest ownership and quota ledgers, Service Bus workers, and budget-metered Foundry/Speech.

## Trust is part of the product

Synthetic scenes are labelled. Video scores remain unknown when not observed. Uncalibrated pixels are not metres; missing ball observations are not invented trajectories. The 2.5D view is a ground-plane visualization, not recovered pose or ball height. General-purpose detector output is not an independently measured football-accuracy result. Reference xT/xG estimates remain unvalidated.

**Release status:** a connected Azure hackathon pilot is deployed alongside the local reference runtime. Its completed synthetic analysis and private backup restore are verified; a configured provider flag is not proof of successful generation or interactive Microsoft sign-in. See the [dated release evidence and limitations](docs/azure/deployment.md). No enterprise SLA, 100-user capacity result, football-model accuracy guarantee, hard Azure spending cap, or production-readiness certificate is claimed.

## Explore further

| Understand | Build and evaluate | Review the release |
| --- | --- | --- |
| [User guide](docs/product/user-guide.md) | [Development](docs/development/getting-started.md) | [Judging guide](docs/submission/judging-guide.md) |
| [Architecture](docs/architecture/overview.md) | [Testing and evidence](docs/development/testing.md) | [Azure release status](docs/azure/deployment.md) |
| [Agent workflow](docs/architecture/agent-workflow.md) | [API reference](docs/reference/api.md) | [Privacy and security](docs/operations/privacy-and-security.md) |

Original application source is [MIT licensed](LICENSE). [Third-party notices](docs/reference/third-party-notices.md) cover separate dependency, model, and footage rights. Contributions and vulnerability reports follow [Contributing](.github/CONTRIBUTING.md) and [Security](.github/SECURITY.md).
