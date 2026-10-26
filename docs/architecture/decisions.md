# Engineering decisions

[Documentation](../README.md) · [Architecture](overview.md)

| Decision | Reason and consequence |
| --- | --- |
| Deterministic first, generative second | Metrics/evidence remain inspectable when a model is unavailable; generation changes presentation, not facts. |
| Seeded, versioned synthetic scenarios | Reproducible demonstrations and golden tests without asserting access to proprietary tracking. Existing recipe versions remain resumable. |
| Survey before physical claims | Image percentages alone do not establish metres, speed, formation, or reconstructed pitch geometry. |
| Fail explicitly on unavailable CV | A missing local model cannot silently become successful observed football analysis. Motion preview remains development-only and labelled. |
| One playback clock | Video, pitch, moment seeking, and commentary must reference source time rather than WebSocket arrival. |
| Private same-origin media | Source and clips remain authorized resources; playback requires byte-range and ownership checks. |
| Single-host reference persistence | SQLite WAL plus a filesystem runner lease provides local durability, not multi-replica coordination. |
| Planned PostgreSQL/Blob/Service Bus migration | Separate request serving from native analysis while retaining event IDs, ownership, recovery, and source clocks. Integration/staging remains required. |
| App workspace is not an Entra directory | Personal accounts may share a consumer directory; application ownership must remain distinct. Identity migration is not yet a verified release. |
| IP allowance is not ownership | Planned guest controls share a three-use IP allowance but isolate browser-owned analyses; no claiming results through IP alone. |
| Measured release gates | Missing independent quality, restore, provider, locale, or load evidence must not become a readiness claim. |

## Historical design reconciliation

The retired root specifications mixed implementation facts with proposals. Useful principles now live in these architecture documents, the [user guide](../product/user-guide.md), and [testing methodology](../development/testing.md).

Earlier Cosmos DB persistence, separate services for every conceptual “agent,” automatic translation-service assumptions, studio-mode layouts, and pseudo-3D claims are not the implemented reference architecture. The approved target uses PostgreSQL, private Blob, and bounded queued workers. The actual UI is match/moments/story, not the old proposed three-rail dashboard.

Historical acceptance targets were goals, not measured results. Tracking accuracy, tactical precision/recall, independent camera holdouts, calibrated xT/xG, reviewed multilingual output, and staging reliability still require evidence.

## Repository preservation

Exact inventoried logs, generated WAVs, historical plans, root probes/launcher, and tracked source/derived media were backed up with SHA-256 verification outside Git before release-tree removal. Supported diagnostics replace the probes; the standard Uvicorn entry point replaces the launcher.

This cleanup does not erase prior Git history, prove footage rights, or authorize a history rewrite. Private `.env`, ongoing preview data, and unrelated source work must remain untouched.
