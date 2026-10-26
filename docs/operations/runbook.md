# Tactiq operations

[Documentation](../README.md) · [Privacy and security](privacy-and-security.md) · [Cloud release gates](../azure/deployment.md)

## Deployment boundary

This implementation is a **single-host, single-API-process** deployment with an exclusive durable SQLite job runner, a bounded thread pool, and private local media objects. It is not a distributed worker system. Keep Uvicorn at one worker and attach a persistent writable volume for both database and media.

`Dockerfile`, `apps/web/Dockerfile`, and `compose.yml` package the API and same-origin web proxy. Compose defaults to local mode. For a production host, terminate HTTPS at a trusted gateway, set `APP_ENV=production`, configure authentication, and explicitly allow the public HTTPS browser origin. Never expose local unauthenticated mode publicly.

Do not deploy until the measured readiness report passes `tactiq.evaluation gate` and the applicable deployment approval is recorded. A container image, an HTTP 200, and a green unit suite are not release approval.

The approved PostgreSQL/Blob/Service Bus, Microsoft identity/trial, and Agent Framework migration is planned/in progress. Do not scale this reference API across replicas or describe it as the verified cloud target. Region/SKUs, budget headroom, Linux runtime, and staging evidence remain unresolved.

## Access control

Set `TACTIQ_AUTH_ENABLED=true`. `TACTIQ_API_TOKENS_JSON` is an array of:

```json
[
  {
    "token_sha256": "<SHA256 of a separately distributed high-entropy token>",
    "tenant_id": "studio",
    "subject": "operator",
    "role": "admin"
  }
]
```

Generate high-entropy credentials outside source control and distribute them securely. Store only digests in configuration. Roles are `viewer`, `analyst`, and `admin`; tokens are scoped to a tenant and subject. This is scoped API-token access, not enterprise SSO.

- Viewers read owned-tenant matches/media and request explanations or speech.
- Analysts also create/analyze matches, control replay scenarios, and inspect commentary audits.
- Administrators also inspect operational metrics and delete inactive matches.
- Foreign-tenant match/job identifiers return not found.

The browser exchanges a bearer credential for an HttpOnly, SameSite session cookie. Authentication scheme capitalization is insignificant. Production requires secure cookies. Prefer the same-origin Next proxy; avoid cross-site cookie deployments. Cookie-authenticated writes and WebSocket sessions validate Origin. Credentials never belong in WebSocket URLs, logs, job payloads, screenshots, or committed files.

Rotate a compromised credential by replacing/removing its digest and restarting the API. Session-cookie expiry does not revoke an independently distributed bearer token; revoke the digest as well.

## Capacity, failure, and recovery

- Two job workers and eight total pending/running jobs are defaults.
- HTTP heavy-operation admission is bounded separately.
- Request bodies and video downloads are bounded. The default input cap is 200 MiB.
- The cache defaults to 32 matches; inactive snapshots are evicted and restored on demand. Pinned requests, viewers, and running analyses are not evicted.
- Tenant storage is capped at 100 match records. Operators must delete/archive old records before admitting more.
- Subscriber queues are bounded. Slow viewers receive `resync.required` and reload the authoritative state.
- Replaying a committed scenario event is idempotent. Paused scenarios resume after their last sequence.
- Scenario recipe versions are persisted. v2 adds multi-phase actions and generated trajectories; v1 snapshots keep their original recipe. Preparing the data is not the user's playback clock, and tracking samples are not football actions.

Jobs persist JSON options and private input paths, not credentials. Startup explicitly recovers interrupted/queued jobs. Recovery is **at least once**: completed operation IDs reuse the existing completed match. Only explicitly retryable exceptions consume the transient retry path. Exhausted work enters dead letter.

Cancellation cannot undo native inference, FFmpeg execution, or media already written. The runner reports cancellation after the active handler returns; a completed side effect may still be present in match history.

Cancelling a queued upload removes its pending input. On startup, recovery is followed by exact-name cleanup of managed pending files not referenced by unfinished jobs; unrelated files and symlinks are not deleted. Match completion persists and detaches its result atomically before cache eviction becomes possible. Media requests retain their match pin until the response body finishes or disconnects, so deletion cannot interrupt an active stream.

YouTube jobs use canonical IDs and operation-owned `.part`/`.bin` files; signed provider URLs remain transient. The pinned resolver/EJS pair requires Node 22+ on the API host. Its isolated child has no cloud credentials, user configuration, arbitrary plugins, cookies or remote-component updates. Resolver networking and downloaded Googlevideo redirects remain HTTPS/public-host restricted. Protected, login-required, live or otherwise unavailable videos fail with a curated upload-alternative message; do not work around provider access restrictions.

After a crash: restore the volume, start one API runner, inspect job state and readiness, and verify the latest match's event sequence/replay. A queued source file must remain available. Do not remove `pending` files while their jobs are active.

## Persistence and retention

`TACTIQ_DATABASE_PATH` controls SQLite metadata; `TACTIQ_MEDIA_DIR` controls `pending`, `sources`, and `clips`. WAL transactions atomically save match state, events, insights, replay, preferences, and audits. Media must live on the same persistent volume lifecycle as its metadata.

Use an encrypted volume with restricted filesystem permissions. The authenticated media routes expose registered source/clip objects, not a public directory listing. Resolved media paths must belong to the requested match's directory; symlink entries and cross-match associations are rejected, including after restoring metadata. Job records retain terminal status and their analysis result for operational recovery; deleting a match does not purge that separate job history. Set an operational retention policy for the database as well as media.

Back up with jobs paused/stopped. Use SQLite's backup facility or a filesystem snapshot that includes WAL state, and back up media from the same checkpoint. Restore to an isolated host and verify tenant boundaries, source playback, insight references, and interrupted-job recovery before returning traffic.

An administrator can delete a known inactive match through `DELETE /api/v1/matches/{id}`. Deletion validates exact match-owned directories and refuses symlinks or unexpected nested entries. Never delete the entire media root as a maintenance shortcut. Before large retention changes, back up and inspect specific record/object IDs.

## Observability and alerts

Every HTTP response carries a request ID. `/api/v1/operations/metrics` separates HTTP, event-processing, and job measurements. `/status` evaluates bounded in-process samples; `/prometheus` exports aggregate counters/latency values without request content. The UI's collapsed administrator operations panel displays the same measurements.

Fresh processes report `insufficient_samples`, not a fictional healthy SLO. The default checks cover API p95 latency, 5xx error budget, and analysis-job failures. Thresholds and samples are operational indicators, not proof of football-model quality.

Webhook delivery is opt-in. Configure `TACTIQ_MONITOR_API_URL`, an administrator-scoped `TACTIQ_MONITOR_ACCESS_TOKEN`, and an HTTPS `TACTIQ_ALERT_WEBHOOK_URL`, then run:

```powershell
.\.venv\Scripts\python.exe -m tactiq.operations --once
```

Omit `--once` for a supervised watcher. Alert delivery failures are explicit. Verify the receiver in staging and record evidence before setting `staging_alerts_verified` in a release report.

## Incident handling

1. Use the request/job ID to identify scope; never paste tokens or full private request bodies into reports.
2. Pause new analysis admission or revoke the affected credential.
3. Preserve database/media backups before changing persistent data.
4. Inspect dead-letter work, decode/inference failures, disk capacity, and the configured model checksum.
5. Recover with one runner, confirm tenant isolation and replay/source integrity, then reopen admission.

Foundry failures retain deterministic explanations. Speech failures retain text and disclose fallback/unavailability. CV configuration/decoding failures must not silently become model-tracking success. Uncalibrated or lost-camera frames withhold physical geometry rather than pretending image percentages are pitch measurements.

Video ingestion now defaults to model tracking. The explicit motion preview produces no observed ball or supported football claims. Ball ROI/temporal refinement is still general-purpose model inference: inspect observed/predicted sample counts, scene resets and independent annotations, not a blended confidence percentage. Camera cuts require a fresh survey; synthetic pitch coordinates are labelled separately from surveyed calibration.

Manual deep explanations pause replay and have audio priority; automatic commentary starts only with playback. Successful scripts/audio use bounded tenant-scoped five-minute caches. Foundry HTTP 401/403/404/429 opens a five-minute generation cooldown and is exposed as a labelled provider failure, not a live-model success. Configuration/voice-pair capability flags do not prove remote service health. Changing the frontend speech kill switch still requires a rebuild.

Invalid video containers fail before automatic match creation. The legacy motion-analysis fallback is only a labelled, source-timed synthetic preview for otherwise decodable media, with zero observation confidence and no evidence clips or supported narrative/recap claims. That restriction is independent of configurable confidence thresholds. It is not a fallback for model-based tracking.

Keep live-provider checks separate from offline endpoint coverage. `tools\check_live_services.py` requires explicit billable permission and writes sanitized outcomes; a rate-limited Foundry response with deterministic fallback is a failed live-model check, not a successful model generation. Profile lookup through `DefaultAzureCredential` is separate from key-auth model inference. Environment fallback instructions do not verify deployed profile versions. Rotate any credential exposed in chat before production and keep replacement values in ignored local configuration or the deployment secret store.

Public video downloads use incremental reads and an absolute connection deadline, including stalled headers or trickled content. This does not turn an uploaded file into licensed footage; rights and independent annotations remain the operator's responsibility.

## Remaining release evidence

Record licensed, independently annotated football validation, holdout camera landmarks, target-deployment narrative evaluation, human review of enabled languages/voices, measured replay synchronization, held-out xT/xG calibration, a security review, and staging restore/alert checks. General-purpose COCO inference and synthetic regression fixtures do not establish those outcomes.

Run the reviewed readiness report through the CLI/CI gate before deployment. This repository does not automatically provision cloud resources, configure a production identity provider, or perform a live release.

## Cloud operations and rollback — required before cutover

The following is the approved operational acceptance checklist, not a deployed runbook:

1. Record exact release/image digests, approved target/region, scoped identities, secret references, private database/media configuration, and a dated migration checkpoint.
2. Verify transactional job/outbox claims, duplicate delivery, lease renewal/expiry, cancellation, dead-letter handling, and revision-based replay recovery against the real services.
3. Verify personal/work/school sign-in, workspace boundaries, guest promotion/expiry, and centralized quota/budget admission across replicas. Never trust unverified forwarding headers as the client IP.
4. Exercise database/media backup and isolated restoration; document recoverable-copy retention. Check health/queue/latency/spending alerts with the real receiver.
5. For rollback, pause admission, preserve the authoritative database/media and migrations, restore a compatible release or tested checkpoint, then verify tenant isolation and source/replay integrity before reopening. Do not route an authoritative cloud workload to a stale local SQLite fallback.

Do not execute Azure provisioning, rotation, migration, or rollback solely because this checklist exists. Target/cost, resource writes, and public cutover require their separate approvals.
