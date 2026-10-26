import assert from "node:assert/strict";
import test from "node:test";
import { frameIndexAt, mergeReplay, replayAt, trajectoryPoints } from "../lib/replay.ts";
import type { ReplayFrame, ReplayPayload } from "../lib/match-types";

function frame(time: number, x: number, inferred = false): ReplayFrame {
  const ball = { track_id: "ball_1", team_id: null, confidence: 0.9, inferred, position: { x, y: 50 } };
  return {
    timestamp_ms: time, sequence: time + 1, score: {}, possession_team_id: null, tactics: {},
    tracking: { calibrated: true, players: [], ball, ball_position: ball.position },
  };
}

test("binary search never selects a future observation", () => {
  assert.equal(frameIndexAt([frame(100, 10), frame(225, 20)], 99), -1);
  assert.equal(frameIndexAt([frame(100, 10), frame(225, 20)], 200), 0);
  assert.equal(frameIndexAt([], 200), -1);
});

test("incremental replay keeps history and replaces an updated last timestamp without duplicates", () => {
  const previous: ReplayPayload = { match_id: "same", duration_ms: 125, processed_until_ms: 125,
    source_url: null, team_display_names: {}, metadata: {}, insights: [], frames: [frame(0, 0), frame(125, 10)] };
  const replacement = { ...frame(125, 20), sequence: 127 };
  const next = { ...previous, after_sequence: 126, frames: [replacement, frame(250, 30)], processed_until_ms: 250 };
  const merged = mergeReplay(previous, next);
  assert.deepEqual(merged.frames, [previous.frames[0], replacement, frame(250, 30)]);
  assert.deepEqual(mergeReplay(previous, { ...previous, after_sequence: 126, frames: [] }).frames, previous.frames);
  assert.throws(() => mergeReplay({ ...previous, match_id: "different" }, next), /resynchronize/);
  const full = { ...next, after_sequence: 0 };
  assert.equal(mergeReplay(previous, full), full);
});

test("8fps replay interpolates against the video clock with sample age below 150ms", () => {
  const frames = [frame(0, 0), frame(125, 25), frame(250, 50)];
  for (let time = 0; time < 250; time += 5) {
    const sample = replayAt(frames, time);
    assert.ok(sample.sampleLagMs! < 150);
    assert.ok(Math.abs(sample.frame!.tracking.ball!.position!.x - time / 5) < 0.0001);
  }
});

test("occlusion and calibration gaps do not invent trajectories", () => {
  const frames = [frame(0, 0), frame(125, 25, true), frame(250, 50)];
  assert.equal(trajectoryPoints(frames, 250, "ball_1").length, 1);
  assert.equal(replayAt(frames, 900).frame!.tracking.ball, null);
  const unknown = { ...frame(125, 25), tracking: { calibrated: false, players: [] } };
  assert.equal(replayAt([frame(0, 0), unknown], 100).interpolated, false);
});

test("changing ball identity or calibration survey never synthesizes a connecting trajectory", () => {
  const first = frame(0, 0);
  const second = frame(125, 80);
  second.tracking.ball!.track_id = "different_ball";
  const switched = replayAt([first, second], 60);
  assert.equal(switched.frame!.tracking.ball, null);
  assert.equal(switched.frame!.tracking.ball_position, null);
  second.tracking.ball!.track_id = "ball_1";
  first.tracking.calibration = { revision: 1 };
  second.tracking.calibration = { revision: 2 };
  assert.equal(replayAt([first, second], 60).interpolated, false);
  assert.equal(trajectoryPoints([first, second], 125, "ball_1").length, 1);
  first.tactics.team_shapes = {
    team_a: { width_m: 40, depth_m: 30, observed_players: 11, formation_estimate: "4-3-3",
      formation_status: "observed", possession_phase: "in_possession" },
  };
  assert.deepEqual(replayAt([first], 800).frame!.tactics.team_shapes, {});
});
