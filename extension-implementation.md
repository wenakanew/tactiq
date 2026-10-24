# Tactiq — Final Extension Implementation Plan (Last 5 Steps to Production)

## Purpose
This document defines the final extension workstreams required before production launch. It converts the remaining gap list into a sequenced implementation plan with deliverables, acceptance criteria, and go/no-go gates.

---

## Step 1 — True Computer Vision Stack

### Implementation Status (Current)
- ✅ Added initial `cv-ingest-v1` service module with:
   - frame sampling
   - motion-based detections
   - multi-object nearest-neighbor ID tracking
   - basic ball occlusion recovery (short-horizon extrapolation)
   - basic team classification from jersey color dominance (`team_a`/`team_b`/`unknown`)
   - baseline broadcast calibration confidence + homography payload
- ✅ Added API endpoint `POST /api/v1/cv/ingest/upload` for CV-track extraction.
- ✅ Added unit tests for core CV utility functions (team classification, pitch mapping).
- ⏳ Remaining: replace heuristic detector/tracker with model-based pipeline and calibrate against benchmark clips.

### Scope
- Real player detection + multi-object tracking with stable IDs across frames.
- Real ball detection/tracking with re-identification under occlusion.
- Team classification from jersey/color/appearance cues.
- Broadcast camera calibration to map image space → pitch coordinates.

### Implementation Tasks
1. Build `cv-ingest` pipeline service:
   - frame extraction + batching
   - detector inference
   - tracker association
   - ball-specific tracking fallback model
2. Introduce track-state schema:
   - `track_id`, class (`player`/`ball`), confidence, frame time, bbox, pitch coords
3. Add team assignment pipeline:
   - dominant-color embeddings
   - cluster-by-team with temporal smoothing
   - low-confidence unknown class handling
4. Add camera calibration module:
   - line/keypoint detection
   - homography estimation
   - confidence-scored transforms
5. Integrate outputs into existing `cv_live` state reducer.

### Deliverables
- CV service with API contract for track outputs.
- Pitch-mapped player/ball trajectories in live state.
- Team labels with per-frame confidence.

### Acceptance Criteria
- Player ID consistency >= 90% over 15+ second windows.
- Ball track recovery after short occlusion <= 1.5 seconds.
- Team classification precision >= 95% on validation set.
- Calibration reprojection error below agreed threshold.

---

## Step 2 — Advanced Football Intelligence

### Scope
- xThreat/xG chain modeling.
- Formation and team-shape detection.
- Pressing trap / overload recognition.
- Transition-phase classification (build-up, transition, settled attack, defensive reset).

### Implementation Tasks
1. Add possession-chain builder from event + CV stream.
2. Add `xT` and `xG` estimators:
   - per-action value delta
   - sequence accumulation
3. Add formation/shape inference:
   - rolling centroid + line segmentation
   - out-of-possession vs in-possession variants
4. Add tactical pattern detectors:
   - overload on flank, press trigger, trap collapse
5. Extend insight taxonomy and provenance metrics.

### Deliverables
- New insight types + confidence outputs.
- Tactical-state overlays and explanatory metrics.

### Acceptance Criteria
- Detectors produce stable outputs across repeated runs.
- Insight precision/recall meets evaluation baseline.
- All insights include provenance and supporting windows/events.

---

## Step 3 — Replay and Visualization Upgrades

### Scope
- True 2.5D/3D scene reconstruction (not pseudo overlays).
- Trajectory trails and tactical lane rendering.
- Event scrubber with synchronized clip + telemetry + insight timeline.

### Implementation Tasks
1. Implement 2.5D/3D projection layer from calibrated camera + tracked entities.
2. Add trajectory history buffers (ball + top-N players).
3. Add tactical lane renderer (channels, congestion, switch opportunities).
4. Build synchronized scrubber:
   - seek by event, insight, or timestamp
   - lock video frame with telemetry snapshot
5. Optimize frontend performance (virtualization + throttled redraw).

### Deliverables
- New replay canvas module.
- Timeline scrubber with deterministic sync behavior.

### Acceptance Criteria
- Scrubber desync < 150ms over full clip.
- 60 FPS target on supported devices (or documented fallback).
- Replay views pass usability checks for analyst and fan modes.

---

## Step 4 — Production Hardening

### Scope
- Auth, tenancy, RBAC.
- Persistent storage for matches, events, insights, clips, audits.
- Queue-based async execution for heavy CV/video jobs.
- Observability, SLOs, alerts.
- Security hardening (rate limit, abuse controls, review).

### Implementation Tasks
1. Add authentication + authorization:
   - tenant isolation
   - role matrix (`viewer`, `analyst`, `admin`)
2. Add persistence layer:
   - relational metadata store
   - blob/object storage for media
3. Add job orchestration:
   - background workers + retries + dead-letter queue
4. Add observability:
   - tracing, metrics, logs
   - SLOs for ingest latency, insight freshness, API reliability
5. Security program:
   - throttling/rate limits
   - input hardening
   - dependency/SAST/DAST scans

### Deliverables
- Production deployment architecture.
- Runbooks, dashboards, and incident response playbook.

### Acceptance Criteria
- Multi-tenant access control verified by tests.
- No critical findings in security review.
- SLO dashboards active and alerting validated.

---

## Step 5 — Model Quality and Evaluation

### Scope
- Benchmarks for event/CV accuracy.
- Narrative faithfulness/hallucination scoring.
- Multilingual regression quality.
- Continuous evaluation in CI/CD.

### Implementation Tasks
1. Build gold datasets:
   - annotated CV tracking clips
   - tactical event truth sets
2. Add automated evaluation suite:
   - tracking metrics (IDF1/MOTA-like equivalents)
   - insight correctness metrics
3. Add narrative evaluation:
   - factual grounding checks vs provenance
   - hallucination and contradiction tests
4. Add multilingual quality tests:
   - translation fidelity and domain terminology checks
5. Gate deployments with eval thresholds in CI/CD.

### Deliverables
- Evaluation reports per build.
- Release gates tied to quality thresholds.

### Acceptance Criteria
- All release thresholds pass on main branch.
- No regression beyond agreed margin on critical metrics.

---

## Execution Sequence and Readiness Gates

### Suggested Sequence
1. Step 1 (CV stack)  
2. Step 2 (advanced intelligence)  
3. Step 3 (replay/visualization)  
4. Step 4 (production hardening)  
5. Step 5 (quality/evaluation, then launch gate)

### Go/No-Go Gate Before Production
Production promotion is allowed only when:
- Step 1–5 acceptance criteria are all met.
- Security review has no unresolved critical/high issues.
- SLO dashboards and alerts are validated in staging.
- CI/CD evaluation gates are enforced and passing.

---

## Risks and Mitigations

- **Broadcast variability risk** (camera angles, quality):
  - Mitigate with calibration confidence fallback paths and model ensembles.
- **Real-time latency risk** (CV + rendering):
  - Mitigate with queue isolation, batching, and tiered quality modes.
- **Narrative trust risk** (hallucinations):
  - Mitigate with strict provenance-bound generation and scoring gates.
- **Operational risk** (load spikes):
  - Mitigate with autoscaling, backpressure, and rate limiting.

---

## Definition of Done (Program Level)
Tactiq is production-ready when CV tracking is real (not synthetic-derived), tactical intelligence is benchmarked, replay sync is reliable, platform security/observability are operationalized, and all model quality gates pass continuously in CI/CD.
