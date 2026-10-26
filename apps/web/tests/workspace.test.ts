import assert from "node:assert/strict";
import test from "node:test";
import { readWorkspaceReference, saveWorkspaceReference, WORKSPACE_REFERENCE_KEY } from "../lib/workspace-session.ts";
import { clockLabel, evidenceMetrics, teamMonogram, type Insight } from "../lib/workspace-types.ts";
import { containedMediaRect } from "../lib/media-geometry.ts";
import { automaticAudioIsCurrent, automaticMoment, momentAt, type PlaybackSnapshot } from "../lib/playback-commentary.ts";
import { AnalysisOperations } from "../lib/analysis-operation.ts";
import { cloudSessionFrom, mayAnalyze, mayInspect, mayRead, mayStartAnalysis, retryAfterDeadline } from "../lib/workspace-access.ts";
import { loadCloudReplay } from "../lib/cloud-replay.ts";

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  };
}
const owner = { tenant_id: "studio", subject: "analyst" };
const cloud = { enabled: true as const, microsoft_login_enabled: true, owned_speech_required: true };
const guestSession = { kind: "guest", principal: { ...owner, role: "analyst" },
  trial: { remaining: 3, limit: 3, reset_at: null }, expires_at: 1800000000 };

test("guest ownership grants analysis and replay without becoming local or privileged access", () => {
  const guest = cloudSessionFrom(guestSession, cloud);
  assert.equal(guest.status, "guest");
  assert.equal(mayRead(guest), true);
  assert.equal(mayAnalyze(guest), true);
  assert.equal(mayInspect(guest), false);
  assert.equal(mayRead({ status: "anonymous", principal: null }), false);
  assert.equal(mayAnalyze({ status: "authenticated", principal: { ...owner, role: "viewer" } }), false);
});

test("cloud native-processing allocation gates only new analysis, never ownership or read access", () => {
  const guest = cloudSessionFrom(guestSession, cloud);
  assert.equal(mayStartAnalysis(guest, undefined), false);
  assert.equal(mayStartAnalysis(guest, false), false);
  assert.equal(mayStartAnalysis(guest, true), true);
  assert.equal(mayRead(guest), true);
  assert.equal(mayAnalyze(guest), true);
  assert.equal(mayStartAnalysis({ status: "local", principal: { ...owner, role: "admin" } }, false), true);
  assert.equal(mayStartAnalysis({ status: "authenticated", principal: { ...owner, role: "analyst" } }, undefined), true);
  assert.equal(mayStartAnalysis({ status: "authenticated", principal: { ...owner, role: "viewer" }, cloud }, true), false);
  assert.equal(mayStartAnalysis(guest, true, 2000, 1000), false);
  assert.equal(mayStartAnalysis(guest, true, 2000, 2000), true);
});

test("capacity retry deadlines honor server seconds or HTTP dates and safely bound malformed headers", () => {
  const now = Date.parse("2026-10-25T18:00:00Z");
  assert.equal(retryAfterDeadline("60", now), now + 60_000);
  assert.equal(retryAfterDeadline("Sun, 25 Oct 2026 18:02:00 GMT", now), now + 120_000);
  assert.equal(retryAfterDeadline("Sun, 25 Oct 2026 17:59:00 GMT", now), now);
  for (const header of [null, "", "-1", "later"]) assert.equal(retryAfterDeadline(header, now), now + 60_000);
  assert.equal(retryAfterDeadline("99999999999999", now), now + 86_400_000);
});

test("cloud access fails closed on incomplete ownership and invalid trial values", () => {
  for (const invalid of [
    { ...guestSession, principal: null }, { ...guestSession, kind: "local" },
    { ...guestSession, trial: { remaining: -1, limit: 3, reset_at: null } },
    { ...guestSession, trial: { remaining: 3, limit: 3, reset_at: "tomorrow" } },
    { ...guestSession, expires_at: Number.NaN },
  ]) assert.throws(() => cloudSessionFrom(invalid, cloud));
  const member = cloudSessionFrom({ ...guestSession, kind: "member",
    trial: { remaining: null, limit: 3, reset_at: null } }, cloud);
  assert.equal(member.status, "authenticated");
  assert.equal(member.trial?.remaining, null);
  assert.throws(() => cloudSessionFrom(guestSession, { ...cloud, owned_speech_required: false }));
});

test("cloud replay freezes pagination then keeps its cursor for unmetered incremental reads", async () => {
  const paths: string[] = [];
  const frame = { timestamp_ms: 1000, sequence: 1, score: {}, possession_team_id: null, tracking: {}, tactics: {} };
  const result = await loadCloudReplay("match_owned", null, async (path) => {
    paths.push(path);
    const first = paths.length === 1;
    return Response.json({ match_id: "match_owned", duration_ms: 2000, processed_until_ms: 2000,
      source_url: null, team_display_names: {}, insights: [], metadata: {},
      records: [{ record_id: first ? "a" : "b", payload: { ...frame, sequence: first ? 1 : 2 } }],
      cursor: { revision: 7, after: first ? "a" : "b" }, has_more: first });
  });
  assert.equal(result.payload.frames.length, 2);
  assert.equal(result.after, "b");
  assert.ok(!paths[0].includes("revision="));
  assert.ok(paths[1].includes("revision=7") && paths[1].includes("after=a"));
});

test("cloud replay rejects non-advancing cursors and changing frozen snapshots", async () => {
  const frame = { timestamp_ms: 1000, sequence: 1 };
  for (const revisionChange of [false, true]) {
    let count = 0;
    await assert.rejects(loadCloudReplay("match_owned", null, async () => {
      count += 1;
      return Response.json({ match_id: "match_owned", processed_until_ms: 1000, records: [{ record_id: "a", payload: frame }],
        cursor: { revision: revisionChange ? count : 1, after: "a" }, has_more: true });
    }), revisionChange ? /snapshot/ : /advance/);
  }
});

test("analysis retries reuse an opaque operation across reloads without persisting input", async () => {
  const storage = memoryStorage();
  const operations = new AnalysisOperations(storage);
  const input = { url: "https://example.com/private-footage", options: {} };
  const id = await operations.begin("link", "browser-owner", input);
  assert.equal(await operations.begin("link", "browser-owner", input), id);
  assert.equal(await new AnalysisOperations(storage).begin("link", "browser-owner", input), id);
  assert.ok(!storage.getItem("tactiq.analysis.operation.v1.link")!.includes(input.url));
  operations.complete("link");
  assert.notEqual(await operations.begin("link", "browser-owner", input), id);
});

test("changed inputs and owners cannot reuse another analysis operation", async () => {
  const operations = new AnalysisOperations(memoryStorage());
  const first = await operations.begin("scenario", "owner-a", { scenario: "pressure" });
  const second = await operations.begin("scenario", "owner-a", { scenario: "counter" });
  const third = await operations.begin("scenario", "owner-b", { scenario: "counter" });
  assert.notEqual(first, second);
  assert.notEqual(second, third);
  operations.clear();
  assert.notEqual(await operations.begin("scenario", "owner-b", { scenario: "counter" }), third);
});

test("analysis retry identity remains available when browser storage is disabled", async () => {
  const storage = { getItem() { throw new Error("Unavailable"); }, setItem() { throw new Error("Unavailable"); },
    removeItem() { throw new Error("Unavailable"); } };
  const operations = new AnalysisOperations(storage);
  const id = await operations.begin("upload", "owner", { file: "footage.mp4" });
  assert.equal(await operations.begin("upload", "owner", { file: "footage.mp4" }), id);
  operations.complete("upload");
  assert.notEqual(await operations.begin("upload", "owner", { file: "footage.mp4" }), id);
});

test("workspace navigation remembers identifiers, never extra private data", () => {
  const storage = memoryStorage();
  const value = { owner, matchId: "match_a1", jobId: "job_a2", credential: "do-not-persist", narrative: "private text" };
  saveWorkspaceReference(storage, value);
  const raw = storage.getItem(WORKSPACE_REFERENCE_KEY)!;
  assert.ok(!raw.includes("credential") && !raw.includes("private text"));
  assert.deepEqual(readWorkspaceReference(storage, owner), { owner, matchId: "match_a1", jobId: "job_a2" });
});

test("workspace navigation refuses references belonging to another identity", () => {
  const storage = memoryStorage();
  saveWorkspaceReference(storage, { owner, matchId: "match_a1", jobId: null });
  assert.equal(readWorkspaceReference(storage, { ...owner, subject: "another-user" }), null);
  assert.equal(storage.getItem(WORKSPACE_REFERENCE_KEY), null);
});

test("owner fields cannot collide through delimiter-shaped identity strings", () => {
  const storage = memoryStorage();
  saveWorkspaceReference(storage, { owner: { tenant_id: "a:b", subject: "c" }, matchId: "match_a1", jobId: null });
  assert.equal(readWorkspaceReference(storage, { tenant_id: "a", subject: "b:c" }), null);
});

test("invalid remembered JSON and identifiers are explicitly cleared", () => {
  for (const raw of ["{", "null", "[]", JSON.stringify({ owner, matchId: "../private", jobId: null })]) {
    const storage = memoryStorage();
    storage.setItem(WORKSPACE_REFERENCE_KEY, raw);
    assert.throws(() => readWorkspaceReference(storage, owner), /invalid/);
    assert.equal(storage.getItem(WORKSPACE_REFERENCE_KEY), null);
  }
});

test("a workspace without active work leaves no remembered identifiers", () => {
  const storage = memoryStorage();
  saveWorkspaceReference(storage, { owner, matchId: "match_a1", jobId: null });
  saveWorkspaceReference(storage, { owner, matchId: null, jobId: null });
  assert.equal(storage.getItem(WORKSPACE_REFERENCE_KEY), null);
});

test("navigation does not silently swallow unavailable storage", () => {
  const storage = { getItem() { throw new Error("Storage unavailable"); }, removeItem() {} };
  assert.throws(() => readWorkspaceReference(storage, owner), /Storage unavailable/);
});

test("evidence formatting preserves the primary value while adding provenance units", () => {
  const insight: Insight = { title: "Measured movement", summary: "", confidence: .8,
    evidence: [{ metric: "distance", value: "1.234" }],
    provenance: { metrics: [{ metric: "distance", value: 9, unit: "m" }, { metric: "count", value: 2 }] } };
  assert.deepEqual(evidenceMetrics(insight), [
    { metric: "distance", value: "1.234", unit: "m" }, { metric: "count", value: 2 },
  ]);
});

test("unknown clocks stay unknown rather than becoming fabricated zero timestamps", () => {
  assert.equal(clockLabel(undefined), "—");
  assert.equal(clockLabel(Number.NaN), "—");
  assert.equal(clockLabel(65_000), "1:05");
});

test("team monograms are display metadata, not fabricated club badges", () => {
  assert.equal(teamMonogram("Team A"), "TA");
  assert.equal(teamMonogram("Team B"), "TB");
  assert.equal(teamMonogram("West Ham United"), "WH");
  assert.equal(teamMonogram("Arsenal"), "AR");
  assert.equal(teamMonogram(""), "?");
});

test("portrait tracking overlays use the contained video pixels rather than the letterbox", () => {
  const box = containedMediaRect(400, 420, 576, 1024)!;
  assert.equal(box.height, 420);
  assert.equal(box.width, 236.25);
  assert.equal(box.left, 81.875);
  assert.equal(box.top, 0);
  assert.deepEqual(containedMediaRect(640, 360, 1920, 1080), { left: 0, top: 0, width: 640, height: 360 });
  assert.equal(containedMediaRect(0, 0, 0, 0), null);
  assert.throws(() => containedMediaRect(-1, 400, 100, 100), RangeError);
});

test("automatic commentary follows explicit playback, never future moments or a transport queue", () => {
  const early: Insight = { insight_id: "early", title: "Earlier", summary: "", confidence: .8, evidence: [],
    provenance: { window: { end_ms: 1000 } } };
  const future: Insight = { ...early, insight_id: "future", provenance: { window: { end_ms: 40000 } } };
  const frame = { timestamp_ms: 20000, sequence: 1, score: {}, possession_team_id: null, tracking: {}, tactics: {} };
  const playback: PlaybackSnapshot = { matchId: "test", timeMs: 20000, playing: true, seeking: false,
    rate: 1, intent: 1, epoch: 0, followingCurrent: false, frame };
  assert.equal(momentAt([early, future], 20000)?.insight_id, "early");
  assert.equal(automaticMoment({ ...playback, intent: 0 }, [early, future], -Infinity, new Set()), null);
  assert.equal(automaticMoment({ ...playback, playing: false }, [early, future], -Infinity, new Set()), null);
  assert.equal(automaticMoment({ ...playback, seeking: true }, [early, future], -Infinity, new Set()), null);
  assert.equal(automaticMoment(playback, [early, future], -Infinity, new Set())?.key, "early");
  assert.equal(automaticMoment(playback, [early, future], 15000, new Set()), null);
  assert.equal(automaticMoment(playback, [early, future], -Infinity, new Set(["early", "overview:0"])), null);
  assert.equal(automaticAudioIsCurrent(playback, 14000), true);
  assert.equal(automaticAudioIsCurrent(playback, 13999), false);
  assert.equal(automaticAudioIsCurrent({ ...playback, playing: false }, 20000), false);
  assert.equal(automaticAudioIsCurrent(playback, 20001), false);
});
