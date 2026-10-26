# Challenge, categories and judging evidence

[Documentation](../README.md) · [Demo and judge testing](demo-guide.md) · [Submission description](project-description.md) · [Release evidence](../azure/deployment.md)

This is Tactiq's evidence map against the **Microsoft Premier League Hackathon Official Rules supplied by the entrant on 2026-10-26**, especially sections 4.1 through 4.11 and 6. It is a project assessment, not organizer approval, a legal opinion or a guarantee of eligibility or prizes. Check the current rules on the [hackathon website](https://aka.ms/insidethegame) before submitting; the Official Rules prevail.

## Challenge: one pipeline, five stages

The primary submission transforms **synthetic, football-realistic events** into explainable intelligence and personalized narratives. Real match footage is not required to reproduce it.

| Rule 4.1 stage | Tactiq implementation | Evidence and limits |
| --- | --- | --- |
| **Ingest** | Versioned, seeded scenarios with ordered actions, timestamps, metadata and player/ball trajectories. | [Scenario catalogue](../../services/backend/tactiq/simulator/scenarios.py), [v2 generator](../../services/backend/tactiq/simulator/rich.py). Default seed `42001`, recipe v2; scenario IDs `sustained_pressure`, `rhythm_shift`, `player_influence`. Event identities depend on match/scenario/seed, preserving replayability without claiming identical IDs across distinct matches. |
| **Interpret** | Deterministic match state, statistics and pressure/rhythm/influence/transition insights. | [Intelligence source](../../services/backend/tactiq/intelligence/), [domain](../../services/backend/tactiq/domain/). Tracking samples do not inflate action counts. Synthetic coordinates are scenario truth, not independent real-match measurements. |
| **Explain** | Selected-window evidence feeds grounded narrative and ordered lead/analyst commentary. | [Workflow](../architecture/agent-workflow.md), [actual Foundry receipt](../assets/production/foundry-commentary.json), [actual Framework trace](../assets/production/dual-commentary.json). Numbers/entity/structure checks are not a complete semantic hallucination detector. |
| **Render** | Browser match/moments/story experience, timed replay/insights, evidence panels and exports. | [Product guide](../product/user-guide.md), [API](../reference/api.md), [public pitch video](https://youtu.be/ktO6Mbmkhy4). Suitable to demonstrate a companion-screen experience; no rendering-partner integration, broadcast certification or proven live-video latency is claimed. |
| **Personalize** | Fan/Analyst/Player views, selected-player evidence where available, seven text locales and optional ordered speech. | Same evidence, different depth. Player attribution is withheld when unsupported; multilingual configuration is not independent human review of every locale. No automatic real-player name recognition or implemented favorite-club recommendation engine is claimed. |

Rule 4.1 also calls for narratives, key moments, recaps, data innovation and multilingual storytelling. The seeded scenarios, insight windows, recap chapters and locale controls make these inspectable in the [synthetic demo](demo-guide.md). Events can update the local workspace, but the hosted pilot's fifteen-minute initial dispatch is **not** a real-time broadcast service-level result.

Player tagging, pass difficulty, ball/shot speeds and video auto-eventing are suggested features, not permission to invent statistics. Some simulated events carry travel/speed fields; these are not validated real-video speed measurements. General-purpose video tracking does not prove reliable automatic passes, tackles, identity or shot-event extraction. Present the synthetic pipeline as the challenge solution and rights-cleared video as a separate optional extension.

## Prize positioning and category evidence

The project aims to compete on the complete experience rather than a list of service logos. Under sections 4.2 and 8, a project can win at most **one Grand Prize and one Category Prize**. Category awards are not a sequential fallback if the Grand Prize is missed. Select the category fields actually offered by the submission form; judges may reassign a project.

| Prize / category | Tactiq's case | Evidence boundary |
| --- | --- | --- |
| **Grand Prize: First Place / Second Place** | One end-to-end pipeline connecting synthetic data, deterministic intelligence, explainable AI, replay and audience personalization. | Make all five stages visible in the demo. Real broadcaster adoption, football accuracy and independently measured business impact remain unproven. |
| **Best Azure Cloud Native Integration** | Strongest operational category case: Container Apps/Jobs, Service Bus/outbox, private PostgreSQL/Blob, Key Vault, managed identities, bounded workers and usage ledgers. | [Deployed components, images, analysis and restore evidence](../azure/deployment.md), [resource map](../azure/deployment.md#azure-resource-visualizer). Hosted load, HA and disaster/failure-recovery acceptance remain unverified. |
| **Best Use of Microsoft Foundry Project** | Actual grounded narrative calls under the existing Foundry project and an isolated usage-billed deployment, plus Framework-driven role invocations. | Actual [single](../assets/production/foundry-commentary.json) and [dual](../assets/production/dual-commentary.json) receipts show no fallback on those probes. No measured developer-productivity gain or managed hosted-agent sessions are claimed. |
| **Best Multi-Agent System Prize** | Distinct lead and analyst roles coordinate through immutable evidence, validated lead handoff, ordered output and explicit failure fallback. | [Workflow implementation](../../services/backend/tactiq/agents/workflow.py) and actual two-role trace demonstrate purposeful coordination. No A2A, arbitrary tools, self-repair, or measured superiority over a single-agent baseline is claimed. Section 8 calls this **Best Multi-Agent Orchestration**; use the organizer's current form label. |
| **Best Enterprise Solution Prize** | Private media, tenant/ownership boundaries, nonadmin database roles, budget reservations, restore evidence and transparent limitations outline an enterprise adoption path. | These are implemented controls, not an enterprise-readiness certificate. Interactive sign-in acceptance, independent security review, sustained load, operational recovery and human locale/voice review remain gaps. |

## Judging criteria: five equal 20% weights

Stage One is a pass/fail viability/theme/technology screen. Stage Two scores all five criteria equally; breadth of features does not waive functionality or access requirements.

| Criterion | What to demonstrate | Supporting evidence |
| --- | --- | --- |
| **Technological Implementation (20%)** | Reproducible synthetic generation, separated domain/intelligence/transport layers, maintainable contracts and a functioning cloud path. | [Architecture](../architecture/overview.md), generator source above, [test commands](../development/testing.md), [passing published workflow](https://github.com/wenakanew/tactiq/actions/runs/40370649144). |
| **Agentic Design & Innovation (20%)** | Lead describes evidence; analyst adds supported context after validated handoff. Explain why these responsibilities are separated. | [Agent workflow](../architecture/agent-workflow.md), actual trace/usage and [dual-voice sample](../assets/production/commentary-live.mp3). A role split is demonstrated; comparative effectiveness is not benchmarked. |
| **Real-World Impact & Applicability (20%)** | Help viewers understand why a moment matters, with a potential studio/streaming companion experience and explicit cost/privacy boundaries. | [User perspectives](../product/user-guide.md), [costs](../azure/costs.md), [deployment qualifications](../azure/deployment.md#pilot-qualifications-and-remaining-verification). Potential impact is not a user-study or commercial-adoption result. |
| **User Experience & Presentation (20%)** | Show actual browser interaction, replay, one evidence-backed moment, perspective/language change and recap. | [1:55 pitch](https://youtu.be/ktO6Mbmkhy4), [reproducible judge walkthrough](demo-guide.md), [scoped production evidence](../azure/deployment.md#visible-production-evidence). |
| **Adherence to Hackathon Category (20%)** | Connect a specific category claim to working behavior and actual provider/service use. | Category matrix above. Do not claim Fabric, AKS, MCP runtime integration or other unimplemented technology merely because the rules list it. |

The workflow above passed Python 3.12, Python 3.13, frontend and disposable cloud-contract jobs for published commit `b8d4542eb9adc3b14342279e9ab5e537808afbcb`. Its opt-in `production-readiness` job was skipped. That run is not a certification of these later documentation edits or of production readiness.

## Submission and eligibility checklist

| Rule / item | Current evidence or required action |
| --- | --- |
| **Registration and deadline** | Supplied registration closes October 20, 2026 at noon Pacific; submission closes **October 27, 2026 at 11:59 pm Pacific**. Entrant must confirm registration was completed on time. Documentation cannot establish that. |
| **New project and original ownership (4.3, 4.6, 4.11)** | Source and build history are inspectable. Entrant must attest the project was newly created after registration opened September 29, 2026 at 09:00 Pacific, rather than derived from a disallowed pre-existing project, and owns the original contribution. Commit dates alone are not proof of authorship or eligibility. |
| **Entrant/team eligibility (3, 4.10)** | Entrant must privately confirm eligibility, team size at most four where applicable, authorized representative, conflicts and sponsor-support restrictions. Do not publish identity, eligibility documents or team members' personal data in this repository. |
| **Prior awards and acceptable content (4.4)** | Entrant must confirm the submission has not won another contest and complies with content restrictions. No automated certification is asserted. |
| **Description and public code (4.4)** | [Project description](project-description.md); [repository](https://github.com/wenakanew/tactiq) was verified public on 2026-10-26. Keep it public and enter the URL on the hackathon project page. Local documentation edits are not automatically published. |
| **Demo under two minutes (4.4)** | Entrant-supplied [YouTube pitch](https://youtu.be/ktO6Mbmkhy4) returned accessible player metadata, title `Tactiq Pitch`, duration **115 seconds**, `isPrivate=false` on 2026-10-26. Verify actual signed-out playback, final captions and presentation; metadata is not a frame-by-frame rights review or proof that this is byte-identical to the local render. Add this URL to the hackathon form. |
| **Real functioning footage (4.3, 4.4)** | Local render uses fresh real desktop-browser interaction against the local reference runtime with a synthetic scenario and no mocked API responses. Its Azure audio excerpt is a separately verified provider probe, not a live browser-playback or new cloud-analysis claim. Present that distinction. |
| **Synthetic data and footage permissions (4.7)** | Submission demonstration uses project-generated synthetic data. Optional football footage requires analysis, playback and publication permissions. No league footage is supplied or licensed by the organizer. See rights review below. |
| **Free unrestricted testing (4.8)** | [Local synthetic testing](demo-guide.md#free-local-judge-testing) needs no Tactiq login, subscription or provider key and avoids the cloud daily-admission cap. Azure generation is a separate paid integration, documented with receipts. The public pilot alone does **not** meet an unrestricted-access description. Entrant must validate the complete offered test route and keep it available through judging; if uncapped live paid AI is required for testing, resolve access/budget with the organizer before claiming compliance. |
| **Availability through judging (4.8)** | Keep the submitted test build/source and any promised hosted access available through **November 10, 2026 at 11:59 pm Pacific** (November 11, 07:59 UTC). Existing AI period ends November 25, 11:37:03 UTC, but that does not guarantee remaining allowance, uptime or continued provider access. A guest's 24-hour analysis expiry is not a judging-period access guarantee. |
| **English (4.9)** | Description, instructions and pitch narration/captions are English. The demonstrated Swahili UI switch illustrates localization; supply an English explanation of it, not an untranslated submission. |
| **Final form and later changes (4, 5)** | Entrant must complete the hackathon project fields and confirm receipt before the deadline. Do not assume repository edits submit the entry or that substantive post-deadline changes are permitted. |

### Rights and submission-media review

The [MIT license](../../LICENSE) covers original Tactiq source, not all third-party material. Preserve required dependency/model notices and review provider/media licenses under the [third-party notices](../reference/third-party-notices.md).

The local pitch uses project-generated synthetic scenes, offline synthetic narration and an existing Azure Speech sample, with no background music. That is a provenance description, not a blanket legal clearance. The entrant must confirm public/submission rights for generated audio, the offline voice, software captures and any trademarks or logos. Descriptive technology names do not authorize third-party branding or imply league/Microsoft endorsement.

Earlier real football media may remain in Git history. Removing it from the current tree does not establish publication rights or erase history. Review that material and any actual footage used before submitting; no footage/history cleanup or rights attestation is performed by this documentation update.

## Honest readiness statement

Tactiq has verified challenge-relevant behavior and a connected Azure pilot. It is not documented as fully rules-cleared until entrant attestations, final video/media rights, the submitted free testing route and judging-period availability are confirmed. No prize, category selection, broadcast latency, football accuracy, unrestricted paid inference or enterprise certification is guaranteed.
