import type { ReplayFrame } from "./match-types";
import type { Insight } from "./workspace-types";

export type PlaybackSnapshot = {
  matchId: string; timeMs: number; playing: boolean; seeking: boolean; rate: number;
  intent: number; epoch: number; followingCurrent: boolean; frame: ReplayFrame | null;
};
export type ReplayControls = { pause: () => void; duck: (enabled: boolean) => void };
export type PlaybackInteraction = "play" | "pause" | "seek" | "rate";
export type CommentaryTurn = { role: "lead" | "analyst"; text: string };
export type CommentaryScript = {
  turns: CommentaryTurn[]; provider: string; fallback_used: boolean; depth: "broadcast" | "deep";
  script_id?: string;
  provider_failure?: { reason?: string; status_code?: number | null } | null;
};

export function automaticAudioIsCurrent(playback: PlaybackSnapshot | null, requestedAtMs: number): boolean {
  return Boolean(playback?.playing && !playback.seeking && playback.timeMs >= requestedAtMs
    && playback.timeMs - requestedAtMs <= 6000);
}

export function momentAt(insights: Insight[], timeMs: number): Insight | null {
  let latest: Insight | null = null, latestEnd = -1;
  for (const insight of insights) {
    const end = insight.provenance?.window?.end_ms;
    if (typeof end === "number" && end <= timeMs && end >= latestEnd) {
      latest = insight;
      latestEnd = end;
    }
  }
  return latest;
}

export function automaticMoment(
  playback: PlaybackSnapshot, insights: Insight[], lastStartedMs: number, spoken: ReadonlySet<string>,
): { key: string; insight?: Insight } | null {
  if (!playback.playing || playback.seeking || !playback.intent || !playback.frame
      || playback.timeMs - lastStartedMs < 25000) return null;
  const insight = momentAt(insights.filter((item) => item.provenance?.quality?.eligible !== false), playback.timeMs);
  if (insight?.insight_id && !spoken.has(insight.insight_id)
      && playback.timeMs - (insight.provenance?.window?.end_ms ?? 0) < 45000) {
    return { key: insight.insight_id, insight };
  }
  const key = `overview:${Math.floor(playback.timeMs / 30000)}`;
  return spoken.has(key) ? null : { key };
}
