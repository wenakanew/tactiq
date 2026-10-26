import type { Point, ReplayFrame, ReplayPayload, TrackedEntity, TrackingSnapshot } from "./match-types";

export const MAX_INTERPOLATION_GAP_MS = 500;

export function hasPitchGeometry(tracking: TrackingSnapshot): boolean {
  return Boolean(tracking.calibrated || (tracking.geometry_source === "simulation" && tracking.detection_source === "synthetic"));
}

export function mergeReplay(previous: ReplayPayload | null, incoming: ReplayPayload): ReplayPayload {
  if (!incoming.after_sequence) return incoming;
  if (previous?.match_id !== incoming.match_id
      || previous.frames[previous.frames.length - 1]?.sequence !== incoming.after_sequence) {
    throw new Error("The replay sequence changed. Reopen the match to resynchronize.");
  }
  const first = incoming.frames[0]?.timestamp_ms;
  const retained = first === undefined ? previous.frames : previous.frames.filter((frame) => frame.timestamp_ms < first);
  return { ...incoming, frames: [...retained, ...incoming.frames] };
}

export function frameIndexAt(frames: ReplayFrame[], timeMs: number): number {
  let low = 0;
  let high = frames.length - 1;
  let result = -1;
  while (low <= high) {
    const middle = (low + high) >>> 1;
    if (frames[middle].timestamp_ms <= timeMs) {
      result = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return result;
}

function interpolatePoint(a: Point | null | undefined, b: Point | null | undefined, fraction: number): Point | null {
  if (!a || !b) return null;
  return { x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction };
}

function interpolateEntity(a: TrackedEntity, b: TrackedEntity, fraction: number): TrackedEntity {
  if (a.inferred || b.inferred) return a;
  return {
    ...a,
    position: interpolatePoint(a.position, b.position, fraction),
    image_position: interpolatePoint(a.image_position, b.image_position, fraction),
    inferred: a.inferred || b.inferred,
  };
}

export function replayAt(frames: ReplayFrame[], timeMs: number): {
  frame: ReplayFrame | null; sampleLagMs: number | null; interpolated: boolean;
} {
  const index = frameIndexAt(frames, timeMs);
  if (index < 0) return { frame: null, sampleLagMs: null, interpolated: false };
  const before = frames[index];
  const after = frames[index + 1];
  const lag = Math.max(0, timeMs - before.timestamp_ms);
  if (lag > MAX_INTERPOLATION_GAP_MS) {
    return { frame: { ...before, tactics: { ...before.tactics, team_shapes: {}, lanes: {} },
      tracking: { ...before.tracking, ball: null, players: [], ball_position: null,
        raw_ball_position: null, smoothed_ball_position: null, calibrated: false } },
      sampleLagMs: lag, interpolated: false };
  }
  if (!after || lag === 0 || after.timestamp_ms - before.timestamp_ms > MAX_INTERPOLATION_GAP_MS
      || before.tracking.calibrated !== after.tracking.calibrated
      || before.tracking.geometry_source !== after.tracking.geometry_source
      || before.tracking.scene_id !== after.tracking.scene_id
      || before.tracking.calibration?.revision !== after.tracking.calibration?.revision) {
    return { frame: before, sampleLagMs: lag, interpolated: false };
  }
  const fraction = lag / (after.timestamp_ms - before.timestamp_ms);
  const nextPlayers = new Map((after.tracking.players ?? []).map((player) => [player.track_id, player]));
  const tracking: TrackingSnapshot = {
    ...before.tracking,
    players: (before.tracking.players ?? []).flatMap((player) => {
      const next = nextPlayers.get(player.track_id);
      return next ? [interpolateEntity(player, next, fraction)] : [];
    }),
    ball_position: !before.tracking.ball && !after.tracking.ball
      ? interpolatePoint(before.tracking.ball_position, after.tracking.ball_position, fraction)
      : before.tracking.ball && after.tracking.ball && before.tracking.ball.track_id === after.tracking.ball.track_id
        ? before.tracking.ball.inferred || after.tracking.ball.inferred ? before.tracking.ball_position
          : interpolatePoint(before.tracking.ball_position, after.tracking.ball_position, fraction) : null,
    ball: before.tracking.ball && after.tracking.ball
      && before.tracking.ball.track_id === after.tracking.ball.track_id
      ? interpolateEntity(before.tracking.ball, after.tracking.ball, fraction) : null,
  };
  return { frame: { ...before, timestamp_ms: timeMs, tracking }, sampleLagMs: lag, interpolated: true };
}

export function trajectoryPoints(frames: ReplayFrame[], timeMs: number, trackId: string, space: "pitch" | "image" = "pitch"): Point[] {
  const index = frameIndexAt(frames, timeMs);
  const points: Point[] = [];
  let previousTime: number | null = null;
  let previousCalibration: number | undefined;
  let previousScene: number | undefined;
  for (let i = Math.max(0, index - 40); i <= index; i += 1) {
    const frame = frames[i];
    if (timeMs - frame.timestamp_ms > 5000) continue;
    const entity = frame.tracking.ball?.track_id === trackId
      ? frame.tracking.ball : frame.tracking.players?.find((player) => player.track_id === trackId);
    const point = space === "pitch" ? entity?.position : entity?.image_position;
    if (!point || entity?.inferred || (previousTime !== null && frame.timestamp_ms - previousTime > 500)
        || (previousTime !== null && previousCalibration !== frame.tracking.calibration?.revision)
        || (previousTime !== null && previousScene !== frame.tracking.scene_id)) {
      points.length = 0;
    }
    if (point && entity && !entity.inferred) {
      points.push(point);
      previousTime = frame.timestamp_ms;
      previousCalibration = frame.tracking.calibration?.revision;
      previousScene = frame.tracking.scene_id;
    }
  }
  return points;
}
