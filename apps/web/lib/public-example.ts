import type { ReplayPayload } from "./match-types";
import { CONTENT_LANGUAGES, type Insight } from "./workspace-types";

export type ExampleLanguage = typeof CONTENT_LANGUAGES[number][0];
export type ExampleAudience = "fan" | "analyst";
export type PreparedNarrative = { title: string; body: string; provider: string; fallback_used: boolean };
export type ExampleMoment = Insight & {
  insight_id: string;
  events: Array<{ event_id: string; type: string; match_clock_ms: number }>;
  support: Array<{ metric: string; event_ids: string[] }>;
  narratives: Record<ExampleLanguage, Record<ExampleAudience, PreparedNarrative>>;
};
export type PublicExample = {
  version: 1;
  id: string;
  scenario: string;
  seed: number;
  recipe_version: number;
  provenance: { source: string; narratives: string; agent_workflow: string; limitations: string };
  replay: ReplayPayload;
  insights: ExampleMoment[];
  recaps: Record<ExampleLanguage, Record<ExampleAudience, PreparedNarrative>>;
  audio: { insight_id: string; language: "en-GB"; audience: "fan"; url: string; provider: string; note: string };
};

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function narrative(value: unknown): boolean {
  return object(value) && typeof value.title === "string" && typeof value.body === "string"
    && value.provider === "deterministic" && value.fallback_used === true;
}

function localized(value: unknown): boolean {
  return object(value) && CONTENT_LANGUAGES.every(([language]) => {
    const entry = value[language];
    return object(entry) && ["fan", "analyst"].every((audience) => narrative(entry[audience]));
  });
}

function replayMetadata(value: unknown): boolean {
  return object(value)
    && ["notes", "detection_source"].every((key) => value[key] === undefined || typeof value[key] === "string")
    && ["width", "height"].every((key) => value[key] === undefined
      || (typeof value[key] === "number" && Number.isFinite(value[key]) && value[key] > 0))
    && (value.warnings === undefined || (Array.isArray(value.warnings)
      && value.warnings.every((warning) => typeof warning === "string")))
    && (value.insights_truncated === undefined || typeof value.insights_truncated === "boolean");
}

function replayInsight(value: unknown): boolean {
  if (!object(value) || typeof value.insight_id !== "string" || typeof value.title !== "string"
    || !Number.isFinite(value.window_start_ms) || !Number.isFinite(value.window_end_ms)) return false;
  if (value.provenance === undefined) return true;
  return object(value.provenance) && (value.provenance.quality === undefined
    || (object(value.provenance.quality) && typeof value.provenance.quality.eligible === "boolean"
      && typeof value.provenance.quality.reason === "string"));
}

export function isPublicExample(value: unknown): value is PublicExample {
  if (!object(value) || value.version !== 1 || value.id !== "public-pressure-v1"
    || !object(value.replay) || value.replay.match_id !== value.id
    || value.replay.source_url !== null || !Array.isArray(value.replay.frames) || !value.replay.frames.length
    || !replayMetadata(value.replay.metadata)
    || !Number.isFinite(value.replay.duration_ms) || !Number.isFinite(value.replay.processed_until_ms)
    || !object(value.replay.team_display_names)
    || !Object.values(value.replay.team_display_names).every((name) => typeof name === "string")
    || !Array.isArray(value.replay.insights) || !value.replay.insights.every(replayInsight)
    || !Array.isArray(value.insights) || !value.insights.length
    || !localized(value.recaps) || !object(value.provenance) || !object(value.audio)
    || value.audio.url !== "/examples/pressure-en.wav" || value.audio.language !== "en-GB"
    || value.audio.audience !== "fan" || typeof value.audio.insight_id !== "string") return false;
  return value.insights.every((item) => object(item) && typeof item.insight_id === "string"
    && typeof item.title === "string" && typeof item.summary === "string" && Number.isFinite(item.confidence)
    && Array.isArray(item.evidence) && item.evidence.every((metric) => object(metric)
      && typeof metric.metric === "string" && Number.isFinite(metric.value))
    && Array.isArray(item.events) && item.events.every((event) => object(event)
      && typeof event.event_id === "string" && typeof event.type === "string" && Number.isFinite(event.match_clock_ms))
    && Array.isArray(item.support) && item.support.every((metric) => object(metric)
      && typeof metric.metric === "string" && Array.isArray(metric.event_ids) && metric.event_ids.every((id) => typeof id === "string"))
    && object(item.provenance)
    && object(item.provenance.window) && Number.isFinite(item.provenance.window.start_ms)
    && Number.isFinite(item.provenance.window.end_ms) && localized(item.narratives))
    && value.replay.frames.every((frame) => object(frame) && Number.isFinite(frame.timestamp_ms)
      && Number.isFinite(frame.sequence) && object(frame.tracking) && object(frame.tactics));
}
