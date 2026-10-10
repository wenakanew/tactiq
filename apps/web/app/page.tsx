"use client";

import { useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  AlertCircle,
  CircleDot,
  Download,
  Ear,
  Flame,
  Gauge,
  Loader2,
  Play,
  Radio,
  Search,
  Sparkles,
  Timer,
  Users,
  Volume2,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";

type Scenario = {
  scenario_id: string;
  total_ms: number;
};

type Insight = {
  insight_id?: string;
  type?: string;
  title: string;
  summary: string;
  confidence: number;
  evidence: Array<{ metric: string; value: number | string }>;
  clip?: {
    status?: "ready" | "pending" | "failed" | "unavailable";
    url?: string;
    start_ms?: number;
    end_ms?: number;
    duration_ms?: number;
    reason?: string;
    error?: string;
  };
  provenance?: {
    reason_code?: string;
    confidence?: number;
    window?: { start_ms?: number; end_ms?: number };
    metrics?: Array<{ metric: string; value: number | string; unit?: string }>;
  };
};

type RecapChapter = {
  chapter_id: string;
  title: string;
  summary: string;
  window_start_ms: number;
  window_end_ms: number;
  confidence: number;
  team_id: string;
  type: string;
  provenance?: {
    reason_code?: string;
  };
};

type VideoIngestionResponse = {
  match_id: string;
  source: string;
  extractor: string;
  extractor_confidence: number;
  notes: string;
  event_count: number;
  insight_count: number;
  clips_generated?: number;
  insights: Insight[];
};

type CommentaryAuditAgent = {
  name: string;
  version?: string | null;
  source?: string;
  instructions?: string;
};

type CommentaryAuditEntry = {
  call_id: string;
  timestamp_utc: string;
  match_id: string;
  insight_id: string;
  language: string;
  provider: string;
  fallback_used: boolean;
  primary_text: string;
  secondary_text: string;
  audit?: {
    orchestration_path?: string;
    tuning_version?: string | null;
    agents?: CommentaryAuditAgent[];
  };
};

function parseVoicePairMap(raw: string | undefined): Record<string, { primary?: string; secondary?: string }> {
  if (!raw?.trim()) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") {
      return {};
    }

    const result: Record<string, { primary?: string; secondary?: string }> = {};
    for (const [language, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (!value || typeof value !== "object") {
        continue;
      }
      const entry = value as Record<string, unknown>;
      const primary = typeof entry.primary === "string" ? entry.primary.trim() : "";
      const secondary = typeof entry.secondary === "string" ? entry.secondary.trim() : "";
      result[language] = {
        primary: primary || undefined,
        secondary: secondary || undefined,
      };
    }
    return result;
  } catch {
    return {};
  }
}

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? (typeof window !== "undefined" ? window.location.origin : "");
const DEFAULT_PRIMARY_VOICE = process.env.NEXT_PUBLIC_DEFAULT_PRIMARY_VOICE;
const DEFAULT_SECONDARY_VOICE = process.env.NEXT_PUBLIC_DEFAULT_SECONDARY_VOICE;
const DUAL_FALLBACK_SECONDARY_TEXT = process.env.NEXT_PUBLIC_DUAL_COMMENTARY_FALLBACK_SECONDARY_TEXT;
const CLOUD_SPEECH_ENABLED = (process.env.NEXT_PUBLIC_ENABLE_CLOUD_SPEECH ?? "true").toLowerCase() !== "false";
const VOICE_PAIR_MAP = parseVoicePairMap(process.env.NEXT_PUBLIC_VOICE_PAIR_MAP_JSON);

const MALE_HINTS = [
  "ryan",
  "thomas",
  "henri",
  "rafiki",
  "guy",
  "david",
  "george",
  "james",
  "liam",
  "oliver",
  "male",
  "man",
  "andrew",
  "brian",
  "christopher",
  "eric",
  "jacob",
  "roger",
  "tony",
  "davis",
  "mark",
  "daniel",
  "matthew",
];

const FEMALE_HINTS = ["female", "woman", "aria", "zira", "jenny", "sara", "denise", "ava", "susan", "emma"];

function hasHintToken(value: string, hint: string): boolean {
  const escaped = hint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const tokenRegex = new RegExp(`(^|[^a-z0-9])${escaped}([^a-z0-9]|$)`, "i");
  return tokenRegex.test(value);
}

function voiceHasAnyHint(voice: SpeechSynthesisVoice, hints: string[]): boolean {
  const normalized = `${voice.name} ${voice.voiceURI}`
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase();
  return hints.some((hint) => hasHintToken(normalized, hint));
}

function voicePairFor(language: string): { primary?: string; secondary?: string } {
  const byLanguage = VOICE_PAIR_MAP[language] ?? {};
  return {
    primary: byLanguage.primary ?? DEFAULT_PRIMARY_VOICE,
    secondary: byLanguage.secondary ?? DEFAULT_SECONDARY_VOICE,
  };
}

function browserVoicePair(language: string): { primary: SpeechSynthesisVoice | null; secondary: SpeechSynthesisVoice | null } {
  if (typeof window === "undefined" || !window.speechSynthesis) {
    return { primary: null, secondary: null };
  }

  const voices = window.speechSynthesis.getVoices();
  const localeVoices = voices.filter((v) => v.lang?.toLowerCase().startsWith(language.toLowerCase().split("-")[0]));
  const sourceVoices = localeVoices.length > 0 ? localeVoices : voices;
  const maleVoices = sourceVoices.filter((voice) => voiceHasAnyHint(voice, MALE_HINTS));

  const neutralNonFemaleVoices = sourceVoices.filter((voice) => !voiceHasAnyHint(voice, FEMALE_HINTS));

  const fallbackPool = maleVoices.length > 0 ? maleVoices : neutralNonFemaleVoices.length > 0 ? neutralNonFemaleVoices : sourceVoices;
  const primary = fallbackPool[0] ?? null;
  const secondary = fallbackPool[1] ?? fallbackPool.find((voice) => voice !== primary) ?? primary;
  return { primary, secondary };
}

async function getBrowserVoicesWithRetry(timeoutMs = 1200): Promise<SpeechSynthesisVoice[]> {
  if (typeof window === "undefined" || !window.speechSynthesis) {
    return [];
  }

  const initial = window.speechSynthesis.getVoices();
  if (initial.length > 0) {
    return initial;
  }

  return new Promise((resolve) => {
    let settled = false;
    const complete = () => {
      if (settled) {
        return;
      }
      settled = true;
      window.speechSynthesis.removeEventListener("voiceschanged", onVoicesChanged);
      resolve(window.speechSynthesis.getVoices());
    };

    const onVoicesChanged = () => complete();
    window.speechSynthesis.addEventListener("voiceschanged", onVoicesChanged);
    setTimeout(complete, timeoutMs);
  });
}

function browserStrictMaleVoicePair(language: string): { primary: SpeechSynthesisVoice | null; secondary: SpeechSynthesisVoice | null } {
  if (typeof window === "undefined" || !window.speechSynthesis) {
    return { primary: null, secondary: null };
  }

  const voices = window.speechSynthesis.getVoices();
  const localeVoices = voices.filter((v) => v.lang?.toLowerCase().startsWith(language.toLowerCase().split("-")[0]));
  const sourceVoices = localeVoices.length > 0 ? localeVoices : voices;
  const maleVoices = sourceVoices.filter((voice) => voiceHasAnyHint(voice, MALE_HINTS));

  if (maleVoices.length === 0) {
    return { primary: null, secondary: null };
  }

  const primary = maleVoices[0] ?? null;
  const secondary = maleVoices[1] ?? maleVoices.find((voice) => voice !== primary) ?? primary;
  return { primary, secondary };
}

export default function HomePage() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<string>("sustained_pressure");
  const [matchId, setMatchId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("idle");
  const [eventsCount, setEventsCount] = useState<number>(0);
  const [latestInsight, setLatestInsight] = useState<Insight | null>(null);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [audienceMode, setAudienceMode] = useState<string>("fan");
  const [language, setLanguage] = useState<string>("en-GB");
  const [selectedPlayer, setSelectedPlayer] = useState<string>("a_10");
  const [narrative, setNarrative] = useState<string>("");
  const [narrativeMeta, setNarrativeMeta] = useState<string>("");
  const [recap, setRecap] = useState<string>("");
  const [recapMeta, setRecapMeta] = useState<string>("");
  const [recapChapters, setRecapChapters] = useState<RecapChapter[]>([]);
  const [speechStatus, setSpeechStatus] = useState<string>("idle");
  const [videoStatus, setVideoStatus] = useState<string>("idle");
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [selectedVideoFile, setSelectedVideoFile] = useState<File | null>(null);
  const [videoSummary, setVideoSummary] = useState<VideoIngestionResponse | null>(null);
  const [liveCommentaryEnabled, setLiveCommentaryEnabled] = useState<boolean>(true);
  const [dualCommentaryEnabled, setDualCommentaryEnabled] = useState<boolean>(true);
  const [commentaryAudit, setCommentaryAudit] = useState<CommentaryAuditEntry[]>([]);
  const [commentaryAuditStatus, setCommentaryAuditStatus] = useState<string>("idle");
  const [timelineFilter, setTimelineFilter] = useState<string>("all");
  const [timelineSearch, setTimelineSearch] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);
  const speechQueueRef = useRef<Array<{ primary: string; secondary?: string }>>([]);
  const speakingRef = useRef<boolean>(false);
  const cloudSpeechUnavailableRef = useRef<boolean>(false);
  const activeMatchRef = useRef<string | null>(null);
  const auditRequestSequenceRef = useRef<number>(0);

  const wsUrl = useMemo(() => (API_BASE ? API_BASE.replace("http", "ws") : ""), []);

  async function fetchDualCommentary(
    insightId?: string,
    targetMatchId?: string,
  ): Promise<{ primary_text: string; secondary_text: string } | null> {
    const effectiveMatchId = targetMatchId ?? matchId;
    if (!effectiveMatchId || !API_BASE) {
      return null;
    }

    try {
      const response = await fetch(`${API_BASE}/api/v1/matches/${effectiveMatchId}/commentary/dual`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, insight_id: insightId ?? null }),
      });
      if (!response.ok) {
        return null;
      }
      const payload = (await response.json()) as { primary_text: string; secondary_text: string };
      if (!payload.primary_text || !payload.secondary_text) {
        return null;
      }
      void fetchCommentaryAudit(effectiveMatchId);
      return payload;
    } catch {
      return null;
    }
  }

  async function fetchCommentaryAudit(targetMatchId?: string) {
    const effectiveMatchId = targetMatchId ?? matchId;
    if (!effectiveMatchId || !API_BASE) {
      return;
    }

    const requestSequence = ++auditRequestSequenceRef.current;
    setCommentaryAuditStatus("loading");
    try {
      const response = await fetch(`${API_BASE}/api/v1/matches/${effectiveMatchId}/commentary/debug?limit=10`);
      const isStale =
        requestSequence !== auditRequestSequenceRef.current ||
        activeMatchRef.current !== effectiveMatchId;
      if (isStale) {
        return;
      }
      if (!response.ok) {
        setCommentaryAuditStatus("unavailable");
        return;
      }

      const payload = (await response.json()) as { entries?: CommentaryAuditEntry[] };
      setCommentaryAudit(payload.entries ?? []);
      setCommentaryAuditStatus("ready");
    } catch {
      if (requestSequence !== auditRequestSequenceRef.current || activeMatchRef.current !== effectiveMatchId) {
        return;
      }
      setCommentaryAuditStatus("error");
    }
  }

  async function startDemo() {
    if (!API_BASE) {
      setStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    setStatus("creating match...");
    setInsights([]);
    setLatestInsight(null);
    setEventsCount(0);
    setNarrative("");
    setNarrativeMeta("");
    setRecap("");
    setRecapMeta("");
    setRecapChapters([]);
    setVideoSummary(null);
    setCommentaryAudit([]);
    setCommentaryAuditStatus("idle");
    auditRequestSequenceRef.current += 1;

    if (scenarios.length === 0) {
      const available = (await fetch(`${API_BASE}/api/v1/scenarios`).then((res) => res.json())) as Scenario[];
      setScenarios(available);
    }

    const created = await fetch(`${API_BASE}/api/v1/matches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: selectedScenario, seed: 42001 }),
    }).then((res) => res.json());

    setMatchId(created.match_id);
    activeMatchRef.current = created.match_id;
    setStatus("starting match...");

    await fetch(`${API_BASE}/api/v1/matches/${created.match_id}/start`, {
      method: "POST",
    });

    connectWs(created.match_id);
  }

  function applyVideoSummary(payload: VideoIngestionResponse) {
    setVideoSummary(payload);
    setMatchId(payload.match_id);
    setStatus("completed");
    setEventsCount(payload.event_count);
    setInsights(payload.insights ?? []);
    setLatestInsight(payload.insights?.length ? payload.insights[payload.insights.length - 1] : null);
    setNarrative("");
    setNarrativeMeta("");
    setRecap("");
    setRecapMeta("");
    setRecapChapters([]);
    setCommentaryAudit([]);
    setCommentaryAuditStatus("idle");
    auditRequestSequenceRef.current += 1;
    activeMatchRef.current = payload.match_id;
  }

  async function uploadVideo() {
    if (!API_BASE) {
      setVideoStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    if (!selectedVideoFile) {
      setVideoStatus("select a video file first");
      return;
    }

    setVideoStatus("uploading and extracting events...");
    const form = new FormData();
    form.append("file", selectedVideoFile);
    form.append("source_name", selectedVideoFile.name);

    const response = await fetch(`${API_BASE}/api/v1/video/upload`, {
      method: "POST",
      body: form,
    });

    if (!response.ok) {
      setVideoStatus("video upload failed");
      return;
    }

    const payload = (await response.json()) as VideoIngestionResponse;
    applyVideoSummary(payload);
    setVideoStatus(
      `done • ${payload.extractor} • ${payload.event_count} events • ${payload.clips_generated ?? 0} clips`,
    );
  }

  async function ingestVideoFromLink() {
    if (!API_BASE) {
      setVideoStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    if (!videoUrl.trim()) {
      setVideoStatus("enter a video URL first");
      return;
    }

    setVideoStatus("processing video link...");
    const response = await fetch(`${API_BASE}/api/v1/video/from-link`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: videoUrl.trim() }),
    });

    if (!response.ok) {
      setVideoStatus("video link ingestion failed");
      return;
    }

    const payload = (await response.json()) as VideoIngestionResponse;
    applyVideoSummary(payload);
    setVideoStatus(
      `done • ${payload.extractor} • ${payload.event_count} events • ${payload.clips_generated ?? 0} clips`,
    );
  }

  async function generateNarrative() {
    if (!matchId || !API_BASE) {
      return;
    }

    const response = await fetch(`${API_BASE}/api/v1/matches/${matchId}/narratives`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audience_mode: audienceMode,
        language,
        selected_player_id: audienceMode === "player" ? selectedPlayer : null,
      }),
    });

    if (!response.ok) {
      setNarrative("Narrative unavailable yet. Wait for an insight.");
      setNarrativeMeta("");
      return;
    }

    const payload = await response.json();
    setNarrative(payload.body);
    setNarrativeMeta(
      `${payload.audience_mode} • ${payload.language} • provider=${payload.provider} • fallback=${payload.fallback_used}`,
    );
  }

  async function generateRecap() {
    if (!matchId || !API_BASE) {
      return;
    }

    const response = await fetch(`${API_BASE}/api/v1/matches/${matchId}/recap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audience_mode: audienceMode,
        language,
        selected_player_id: audienceMode === "player" ? selectedPlayer : null,
      }),
    });

    if (!response.ok) {
      setRecap("Recap unavailable yet. Wait for insights or full-time.");
      setRecapMeta("");
      setRecapChapters([]);
      return;
    }

    const payload = (await response.json()) as { body: string; audience_mode: string; language: string; provider: string; fallback_used: boolean; chapters?: RecapChapter[] };
    setRecap(payload.body);
    setRecapMeta(
      `${payload.audience_mode} • ${payload.language} • provider=${payload.provider} • fallback=${payload.fallback_used}`,
    );
    setRecapChapters(payload.chapters ?? []);
  }

  async function playSpeech(primary: string, secondary?: string) {
    if (!primary) {
      setSpeechStatus("nothing to speak");
      return;
    }

    if (!API_BASE) {
      setSpeechStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    const configuredPair = voicePairFor(language);
    let response: Response | null = null;

    if (CLOUD_SPEECH_ENABLED && !cloudSpeechUnavailableRef.current) {
      setSpeechStatus("requesting azure speech...");
      try {
        response = await fetch(secondary ? `${API_BASE}/api/v1/speech/synthesize-dual` : `${API_BASE}/api/v1/speech/synthesize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            secondary
              ? {
                  primary_text: primary,
                  secondary_text: secondary,
                  language,
                  ...(configuredPair.primary ? { primary_voice: configuredPair.primary } : {}),
                  ...(configuredPair.secondary ? { secondary_voice: configuredPair.secondary } : {}),
                }
              : {
                  text: primary,
                  language,
                  ...(configuredPair.primary ? { voice: configuredPair.primary } : {}),
                  rate: "+3%",
                  pitch: "+1st",
                },
          ),
        });
      } catch {
        cloudSpeechUnavailableRef.current = true;
      }
    }

    if (response?.ok) {
      const payload = await response.json();
      const audio = new Audio(`data:${payload.content_type};base64,${payload.audio_base64}`);
      await new Promise<void>((resolve, reject) => {
        audio.onplay = () => setSpeechStatus(`speaking via ${payload.provider}`);
        audio.onended = () => {
          setSpeechStatus("done");
          resolve();
        };
        audio.onerror = () => {
          setSpeechStatus("audio playback error");
          reject(new Error("audio playback error"));
        };
        void audio.play().catch(reject);
      });
      return;
    }

    if (!response || response.status >= 500 || response.status === 0) {
      cloudSpeechUnavailableRef.current = true;
      setSpeechStatus("cloud speech unavailable; using browser voices");
    }

    if (secondary) {
      if (typeof window === "undefined" || !window.speechSynthesis) {
        setSpeechStatus("cloud dual speech unavailable");
        return;
      }

      const loadedVoices = await getBrowserVoicesWithRetry();
      if (loadedVoices.length > 0) {
        // Prime voices for browsers that lazy-load and expose them after first access.
        window.speechSynthesis.getVoices();
      }

      const strictMalePair = browserStrictMaleVoicePair(language);
      if (!strictMalePair.primary) {
        setSpeechStatus("cloud dual speech unavailable (no male browser voice)");
        return;
      }

      window.speechSynthesis.cancel();
      const speakOnce = (text: string, voice: SpeechSynthesisVoice | null, rate: number, pitch: number) =>
        new Promise<void>((resolve, reject) => {
          const u = new SpeechSynthesisUtterance(text);
          u.lang = language;
          u.rate = rate;
          u.pitch = pitch;
          if (voice) {
            u.voice = voice;
          }
          u.onstart = () => setSpeechStatus(`speaking dual via browser male (${language})`);
          u.onend = () => resolve();
          u.onerror = () => reject(new Error("browser speech error"));
          window.speechSynthesis.speak(u);
        });

      try {
        await speakOnce(primary, strictMalePair.primary, 1.04, 0.98);
        await speakOnce(secondary, strictMalePair.secondary, 0.98, 0.9);
        setSpeechStatus("done");
      } catch {
        setSpeechStatus("browser speech error");
      }
      return;
    }

    // Fallback to browser speech if cloud speech is unavailable.
    if (typeof window === "undefined" || !window.speechSynthesis) {
      setSpeechStatus("speech unavailable (cloud and browser unsupported)");
      return;
    }

    window.speechSynthesis.cancel();
    const browserPair = browserVoicePair(language);

    const speakOnce = (text: string, voice: SpeechSynthesisVoice | null, rate: number, pitch: number) =>
      new Promise<void>((resolve, reject) => {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = language;
        u.rate = rate;
        u.pitch = pitch;
        if (voice) {
          u.voice = voice;
        }
        u.onstart = () => setSpeechStatus(`speaking via browser (${language})`);
        u.onend = () => resolve();
        u.onerror = () => reject(new Error("browser speech error"));
        window.speechSynthesis.speak(u);
      });

    try {
      await speakOnce(primary, browserPair.primary, 1.02, 1.03);
      if (secondary) {
        await speakOnce(secondary, browserPair.secondary, 0.98, 0.96);
      }
      setSpeechStatus("done");
    } catch {
      setSpeechStatus("browser speech error");
      throw new Error("browser speech error");
    }
  }

  async function processSpeechQueue() {
    if (speakingRef.current) {
      return;
    }

    const next = speechQueueRef.current.shift();
    if (!next) {
      return;
    }

    speakingRef.current = true;
    try {
      await playSpeech(next.primary, next.secondary);
    } catch {
      // status is already set in playSpeech handlers
    } finally {
      // allow playback callbacks to settle before next queued item
      setTimeout(() => {
        speakingRef.current = false;
        void processSpeechQueue();
      }, 300);
    }
  }

  function enqueueSpeech(primary: string, secondary?: string) {
    speechQueueRef.current.push({ primary, secondary });
    void processSpeechQueue();
  }

  async function speakCurrentText() {
    const scripted = dualCommentaryEnabled ? await fetchDualCommentary() : null;

    if (scripted) {
      enqueueSpeech(scripted.primary_text, scripted.secondary_text);
      return;
    }

    const primary = recap || narrative;
    const secondary = dualCommentaryEnabled
      ? DUAL_FALLBACK_SECONDARY_TEXT
      : undefined;
    enqueueSpeech(primary, secondary);
  }

  function connectWs(id: string) {
    if (!wsUrl) {
      setStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    wsRef.current?.close();
    const ws = new WebSocket(`${wsUrl}/api/v1/ws/matches/${id}`);
    wsRef.current = ws;

    ws.onopen = () => setStatus("live");
    ws.onerror = () => setStatus("websocket error");
    ws.onclose = () => setStatus((current) => (current === "completed" ? current : "disconnected"));

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data) as { type: string; payload?: any };

      if (message.type === "event.created") {
        setEventsCount((count) => count + 1);
      }

      if (message.type === "insight.created" && message.payload) {
        const next = message.payload as Insight;
        setLatestInsight(next);
        setInsights((existing) => [...existing, next]);

        if (liveCommentaryEnabled) {
          if (dualCommentaryEnabled) {
            void fetchDualCommentary(next.insight_id, id).then((scripted) => {
              if (scripted) {
                enqueueSpeech(scripted.primary_text, scripted.secondary_text);
                return;
              }

              enqueueSpeech(
                `${next.title}. ${next.summary}`,
                `That looks dangerous. The confidence is ${Math.round(next.confidence * 100)} percent, and momentum is building.`,
              );
            });
          } else {
            enqueueSpeech(`${next.title}. ${next.summary}`);
          }
        }
      }

      if (message.type === "match.status" && message.payload?.status === "completed") {
        setStatus("completed");
      }
    };
  }

  function confidenceTier(confidence: number): "high" | "mid" | "low" {
    if (confidence >= 0.85) return "high";
    if (confidence >= 0.7) return "mid";
    return "low";
  }

  function liveStatusMeta(value: string): { icon: typeof Wifi; label: string; tone: "on" | "done" | "error" | "idle" } {
    if (value === "live") return { icon: Radio, label: "Live", tone: "on" };
    if (value === "completed") return { icon: Wifi, label: "Full time", tone: "done" };
    if (value.includes("error") || value.includes("missing") || value === "disconnected") {
      return { icon: WifiOff, label: value, tone: "error" };
    }
    return { icon: Loader2, label: value || "idle", tone: "idle" };
  }

  function momentumX(insight: Insight | null): number {
    if (!insight) return 50;
    const type = insight.type ?? "";
    if (type === "turning_point") return 50;
    if (type.includes("pressure")) return 68;
    if (type.includes("rhythm")) return 56;
    if (type.includes("speed") || type.includes("burst")) return 62;
    if (type.includes("player")) return 60;
    return 50;
  }

  function insightIcon(insight: Insight | null) {
    const type = insight?.type ?? "";
    if (type === "turning_point") return Zap;
    if (type.includes("pressure")) return Flame;
    if (type.includes("rhythm")) return Activity;
    if (type.includes("speed") || type.includes("burst")) return Gauge;
    if (type.includes("player")) return Users;
    return Sparkles;
  }

  function formatMs(value?: number): string {
    if (typeof value !== "number") return "—";
    const totalSeconds = Math.floor(value / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  }

  function clipUrlForInsight(insight: Insight | null): string | null {
    const url = insight?.clip?.url;
    if (!url) return null;
    if (url.startsWith("http://") || url.startsWith("https://")) return url;
    if (!API_BASE) return null;
    try {
      return new URL(url, API_BASE).toString();
    } catch {
      return null;
    }
  }

  function hasClipReady(insight: Insight | null): boolean {
    return insight?.clip?.status === "ready" && Boolean(insight.clip.url);
  }

  const turningPoints = insights.filter((i) => i.type === "turning_point").length;
  const heroInsight = latestInsight;
  const heroTier = heroInsight ? confidenceTier(heroInsight.confidence) : "mid";
  const dotX = momentumX(heroInsight);
  const dotIsTurning = heroInsight?.type === "turning_point";
  const HeroIcon = insightIcon(heroInsight);
  const statusMeta = liveStatusMeta(status);
  const StatusIcon = statusMeta.icon;
  const prefersReducedMotion = useReducedMotion();
  const isConnecting = status === "creating" || status === "starting";

  // ---- PitchVision-inspired derived data (all grounded in real insights) ----
  const avgConfidence =
    insights.length > 0 ? insights.reduce((sum, i) => sum + i.confidence, 0) / insights.length : 0;

  const typeCounts = insights.reduce<Record<string, number>>((acc, i) => {
    const key = i.type ?? "other";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  const maxTypeCount = Math.max(1, ...Object.values(typeCounts));

  const keyMoments = (() => {
    const ranked = [...insights].sort((a, b) => b.confidence - a.confidence);
    const picked: Insight[] = [];
    const pickedTimes: number[] = [];
    for (const insight of ranked) {
      const t = insight.provenance?.window?.end_ms ?? 0;
      if (pickedTimes.some((existing) => Math.abs(t - existing) < 3000)) continue;
      picked.push(insight);
      pickedTimes.push(t);
      if (picked.length >= 4) break;
    }
    return picked.sort(
      (a, b) => (a.provenance?.window?.end_ms ?? 0) - (b.provenance?.window?.end_ms ?? 0),
    );
  })();

  const timelineTypes = Object.keys(typeCounts).sort();
  const filteredInsights = insights.filter((insight) => {
    if (timelineFilter !== "all" && (insight.type ?? "other") !== timelineFilter) return false;
    if (timelineSearch.trim()) {
      const term = timelineSearch.trim().toLowerCase();
      return (
        insight.title.toLowerCase().includes(term) ||
        insight.summary.toLowerCase().includes(term) ||
        (insight.type ?? "").toLowerCase().includes(term)
      );
    }
    return true;
  });

  function prettyType(type?: string): string {
    return (type ?? "other").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function downloadBlob(filename: string, content: string, mime: string) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function exportInsightsJson() {
    downloadBlob(
      "tactiq_insights.json",
      JSON.stringify({ match_id: matchId, events_count: eventsCount, insights }, null, 2),
      "application/json",
    );
  }

  function exportInsightsCsv() {
    const lines = ["end_ms,type,title,confidence,reason_code"];
    for (const insight of insights) {
      const title = `"${insight.title.replace(/"/g, '""')}"`;
      lines.push(
        `${insight.provenance?.window?.end_ms ?? ""},${insight.type ?? ""},${title},${insight.confidence},${insight.provenance?.reason_code ?? ""}`,
      );
    }
    downloadBlob("tactiq_insights.csv", lines.join("\n"), "text/csv");
  }

  return (
    <div className="app-shell">
      {/* ============ TOP NAV ============ */}
      <header className="topnav">
        <div className="topnav-brand">
          <div className="topnav-mark" aria-hidden="true">T</div>
          <div>
            <strong>Tactiq</strong>
            <span>Match intelligence</span>
          </div>
        </div>
        <div className="topnav-spacer" />
        <span className={`live-pill live-pill--${statusMeta.tone}`}>
          <StatusIcon
            size={13}
            aria-hidden="true"
            className={statusMeta.tone === "on" ? "icon-pulse" : statusMeta.tone === "idle" && isConnecting ? "icon-spin" : undefined}
          />
          {statusMeta.label}
        </span>
      </header>

      {/* ============ MATCH HEADER ============ */}
      <section className="match-header" aria-label="Match status">
        <div className="scoreboard">
          <div className="team-block">
            <div className="team-badge team-badge--a" aria-hidden="true">A</div>
            <span className="team-name">Team A</span>
          </div>
          <div className="score-block">
            <span className="score-value">{eventsCount > 0 ? "0" : "–"}</span>
            <span className="score-sep">:</span>
            <span className="score-value">{eventsCount > 0 ? "0" : "–"}</span>
          </div>
          <div className="team-block team-block--away">
            <div className="team-badge team-badge--b" aria-hidden="true">B</div>
            <span className="team-name">Team B</span>
          </div>
        </div>
        <div className="match-meta">
          <span className="match-clock">
            {status === "live" ? "● LIVE" : status === "completed" ? "FULL TIME" : "NOT STARTED"}
          </span>
          <span className="status-line">
            {matchId ? `match ${matchId.slice(0, 8)}…` : "no active match"} • {eventsCount} events
            {turningPoints > 0 ? ` • ${turningPoints} turning point${turningPoints === 1 ? "" : "s"}` : ""}
          </span>
        </div>
      </section>

      {/* ============ HERO: pitch + current insight ============ */}
      <section className="hero-grid section" aria-label="Live match view">
        <div className="pitch-panel">
          <span className="pitch-panel-label">
            <CircleDot size={13} aria-hidden="true" /> Live momentum
          </span>
          <div className="pitch-svg-wrap">
            <svg viewBox="0 0 400 260" preserveAspectRatio="xMidYMid slice" role="img" aria-label="Abstract pitch showing momentum direction">
              <defs>
                <radialGradient id="pressureGlow" cx="50%" cy="50%" r="60%">
                  <stop offset="0%" stopColor={dotIsTurning ? "var(--accent)" : "var(--primary)"} stopOpacity="0.35" />
                  <stop offset="100%" stopColor={dotIsTurning ? "var(--accent)" : "var(--primary)"} stopOpacity="0" />
                </radialGradient>
                <linearGradient id="pitchFade" x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor="var(--team-a)" stopOpacity="0.08" />
                  <stop offset="50%" stopColor="transparent" stopOpacity="0" />
                  <stop offset="100%" stopColor="var(--team-b)" stopOpacity="0.08" />
                </linearGradient>
              </defs>
              <rect x="6" y="6" width="388" height="248" rx="10" fill="url(#pitchFade)" stroke="var(--pitch-line)" strokeWidth="2" />
              <line x1="200" y1="6" x2="200" y2="254" stroke="var(--pitch-line)" strokeWidth="2" />
              <circle cx="200" cy="130" r="36" fill="none" stroke="var(--pitch-line)" strokeWidth="2" />
              <rect x="6" y="80" width="46" height="100" fill="none" stroke="var(--pitch-line)" strokeWidth="2" />
              <rect x="348" y="80" width="46" height="100" fill="none" stroke="var(--pitch-line)" strokeWidth="2" />
              {heroInsight ? (
                <circle cx={(dotX / 100) * 388 + 6} cy={130} r="60" fill="url(#pressureGlow)" />
              ) : null}
              {!prefersReducedMotion && heroInsight ? (
                <motion.circle
                  key={`ripple-${heroInsight.insight_id ?? heroInsight.title}`}
                  cx={(dotX / 100) * 388 + 6}
                  cy={130}
                  fill="none"
                  stroke={dotIsTurning ? "var(--accent)" : "var(--primary)"}
                  strokeWidth="2"
                  initial={{ r: 9, opacity: 0.9 }}
                  animate={{ r: 48, opacity: 0 }}
                  transition={{ duration: 1.6, repeat: Infinity, ease: "easeOut" }}
                />
              ) : null}
              <motion.circle
                className="pitch-momentum-dot"
                cy={130}
                r="9"
                fill={dotIsTurning ? "var(--accent)" : "var(--primary)"}
                animate={{ cx: (dotX / 100) * 388 + 6 }}
                transition={{ type: "spring", stiffness: 90, damping: 16 }}
              />
              {audienceMode === "player" && heroInsight ? (
                <motion.circle
                  cx={(dotX / 100) * 388 + 6}
                  cy={130}
                  r="16"
                  fill="none"
                  stroke="var(--focus)"
                  strokeWidth="2"
                  strokeDasharray="4 3"
                  animate={prefersReducedMotion ? {} : { rotate: 360 }}
                  style={{ transformOrigin: `${(dotX / 100) * 388 + 6}px 130px` }}
                  transition={{ duration: 6, repeat: Infinity, ease: "linear" }}
                />
              ) : null}
            </svg>
          </div>
          <div className="pitch-legend">
            <span><i style={{ background: "var(--team-a)" }} /> Team A build-up</span>
            <span><i style={{ background: "var(--team-b)" }} /> Team B build-up</span>
            <span><i style={{ background: "var(--accent)" }} /> Turning point</span>
          </div>
        </div>

        <div className="insight-hero">
          <AnimatePresence mode="wait">
            {heroInsight ? (
              <motion.div
                key={heroInsight.insight_id ?? heroInsight.title}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.28, ease: "easeOut" }}
              >
                <div className="insight-hero-top">
                  <span className="insight-eyebrow">
                    <HeroIcon size={13} aria-hidden="true" />
                    {dotIsTurning ? "Turning point" : "Live intelligence"}
                  </span>
                  <span className="insight-timestamp">
                    <Timer size={12} aria-hidden="true" /> {formatMs(heroInsight.provenance?.window?.end_ms)}
                  </span>
                </div>
                <h2 className="insight-title">{heroInsight.title}</h2>
                <p className="insight-summary">{heroInsight.summary}</p>
                <div className="confidence-row">
                  <div className="confidence-track">
                    <motion.span
                      className={`confidence-fill confidence-fill--${heroTier}`}
                      initial={{ width: 0 }}
                      animate={{ width: `${Math.round(heroInsight.confidence * 100)}%` }}
                      transition={{ duration: 0.5, ease: "easeOut" }}
                    />
                  </div>
                  <span className="confidence-label">{Math.round(heroInsight.confidence * 100)}% confidence</span>
                </div>
                <div className="insight-actions">
                  <button className="btn btn--primary btn--sm" onClick={speakCurrentText}>
                    <Volume2 size={14} aria-hidden="true" /> Listen
                  </button>
                </div>
                {hasClipReady(heroInsight) && clipUrlForInsight(heroInsight) ? (
                  <div className="hero-clip">
                    <video controls preload="metadata" className="clip-player" src={clipUrlForInsight(heroInsight) ?? undefined} />
                    <p className="status-line">
                      clip window {formatMs(heroInsight.clip?.start_ms)}–{formatMs(heroInsight.clip?.end_ms)}
                    </p>
                  </div>
                ) : null}
              </motion.div>
            ) : (
              <motion.div
                key="empty"
                className="empty-hero"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
              >
                {isConnecting ? (
                  <>
                    <Loader2 size={22} className="icon-spin" aria-hidden="true" />
                    <strong>Connecting…</strong>
                    <p>Tactiq is spinning up the match feed.</p>
                  </>
                ) : (
                  <>
                    <strong>No insight yet</strong>
                    <p>Start a scenario to see Tactiq explain the match as it unfolds.</p>
                    <button className="btn btn--primary" onClick={startDemo}>
                      <Play size={14} aria-hidden="true" /> Start scenario
                    </button>
                  </>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </section>

      {/* ============ MATCH OVERVIEW METRICS ============ */}
      <section className="section" aria-label="Match overview">
        <div className="section-head">
          <span className="section-title">Match overview</span>
          <span className="section-hint">Live counts from the intelligence pipeline</span>
        </div>
        <div className="overview-grid">
          <div className="metric-card">
            <span className="metric-label">Insights</span>
            <span className="metric-value">{insights.length}</span>
            <span className="metric-desc">Explained match moments</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Turning points</span>
            <span className="metric-value">{turningPoints}</span>
            <span className="metric-desc">Momentum-shifting moments</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Events</span>
            <span className="metric-value">{eventsCount}</span>
            <span className="metric-desc">Raw match events ingested</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Avg confidence</span>
            <span className="metric-value">{insights.length > 0 ? `${Math.round(avgConfidence * 100)}%` : "—"}</span>
            <span className="metric-desc">Across all insights</span>
          </div>
        </div>
      </section>

      {/* ============ KEY MOMENTS + BREAKDOWN ============ */}
      {insights.length > 0 ? (
        <section className="section moments-grid" aria-label="Key moments and breakdown">
          <div>
            <div className="section-head">
              <span className="section-title">Key moments</span>
              <span className="section-hint">Highest-confidence detections</span>
            </div>
            <div className="moment-list">
              {keyMoments.map((moment) => {
                const MomentIcon = insightIcon(moment);
                return (
                  <button
                    key={`km-${moment.insight_id ?? moment.title}`}
                    type="button"
                    className="moment-card"
                    onClick={() => {
                      setLatestInsight(moment);
                    }}
                  >
                    <span className="moment-time">{formatMs(moment.provenance?.window?.end_ms)}</span>
                    <span className="moment-title">
                      <MomentIcon size={14} aria-hidden="true" /> {moment.title}
                    </span>
                    <span className="moment-meta">
                      {prettyType(moment.type)} · {Math.round(moment.confidence * 100)}% confidence
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <div className="section-head">
              <span className="section-title">Insight breakdown</span>
              <span className="section-hint">What Tactiq detected</span>
            </div>
            <div className="breakdown">
              {Object.entries(typeCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([type, count]) => (
                  <div className="breakdown-row" key={type}>
                    <span className="breakdown-label">{prettyType(type)}</span>
                    <span className="breakdown-track">
                      <motion.span
                        className="breakdown-fill"
                        initial={{ width: 0 }}
                        animate={{ width: `${(count / maxTypeCount) * 100}%` }}
                        transition={{ duration: 0.4, ease: "easeOut" }}
                      />
                    </span>
                    <span className="breakdown-count">{count}</span>
                  </div>
                ))}
            </div>
          </div>
        </section>
      ) : null}

      {/* ============ PERSONALIZATION ============ */}
      <section className="section" aria-label="Personalization">
        <div className="personalize-bar">
          <div className="personalize-group">
            <span className="field-label" id="mode-label">View as</span>
            <div className="segmented" role="group" aria-labelledby="mode-label">
              {(["fan", "analyst", "player"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={audienceMode === mode}
                  onClick={() => setAudienceMode(mode)}
                >
                  {mode === "fan" ? "Fan" : mode === "analyst" ? "Analyst" : "Player"}
                </button>
              ))}
            </div>
          </div>
          <div className="personalize-group">
            <span className="field-label" id="lang-label">Language</span>
            <div className="lang-tabs" role="group" aria-labelledby="lang-label">
              <button type="button" aria-pressed={language === "en-GB"} onClick={() => setLanguage("en-GB")}>
                English
              </button>
              <button type="button" aria-pressed={language === "sw-KE"} onClick={() => setLanguage("sw-KE")}>
                Kiswahili
              </button>
              <button type="button" aria-pressed={language === "fr-FR"} onClick={() => setLanguage("fr-FR")}>
                Français
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ============ PLAYER MODE ============ */}
      {audienceMode === "player" ? (
        <section className="section" aria-label="Player focus">
          <div className="player-panel">
            <div className="player-avatar" aria-hidden="true">
              {selectedPlayer.slice(-2).toUpperCase()}
            </div>
            <div className="player-info">
              <h3>Player {selectedPlayer}</h3>
              <p>
                {heroInsight?.type === "player_influence" || heroInsight?.type === "player_speed_burst"
                  ? heroInsight.summary
                  : "Tracking this player's involvement — their next contribution will appear here as an insight."}
              </p>
            </div>
            <div className="player-select">
              <label htmlFor="player-pick" className="sr-only">
                Focus player
              </label>
              <select id="player-pick" value={selectedPlayer} onChange={(event) => setSelectedPlayer(event.target.value)}>
                <option value="a_10">Team A — #10</option>
                <option value="b_08">Team B — #8</option>
              </select>
            </div>
          </div>
        </section>
      ) : null}

      {/* ============ TIMELINE ============ */}
      <section className="section" aria-label="Match timeline">
        <div className="section-head">
          <span className="section-title">Match timeline</span>
          <span className="section-hint">Every entry is grounded in measurable evidence</span>
        </div>
        {insights.length > 0 ? (
          <div className="explorer-bar">
            <div className="explorer-filter">
              <label htmlFor="timeline-filter" className="sr-only">Filter by insight type</label>
              <select
                id="timeline-filter"
                value={timelineFilter}
                onChange={(event) => setTimelineFilter(event.target.value)}
              >
                <option value="all">All insights</option>
                {timelineTypes.map((type) => (
                  <option key={type} value={type}>
                    {prettyType(type)} ({typeCounts[type]})
                  </option>
                ))}
              </select>
            </div>
            <div className="explorer-search">
              <Search size={14} aria-hidden="true" />
              <label htmlFor="timeline-search" className="sr-only">Search insights</label>
              <input
                id="timeline-search"
                type="search"
                placeholder="Search pressure, rhythm, player…"
                value={timelineSearch}
                onChange={(event) => setTimelineSearch(event.target.value)}
              />
            </div>
            <span className="explorer-count">
              {filteredInsights.length} of {insights.length}
            </span>
          </div>
        ) : null}
        {insights.length === 0 ? (
          <div className="empty-state">No events explained yet — insights will appear here as the match unfolds.</div>
        ) : filteredInsights.length === 0 ? (
          <div className="empty-state">No insights match the current filters.</div>
        ) : (
          <div className="timeline">
            {[...filteredInsights].reverse().map((insight, index) => {
              const major = insight.type === "turning_point";
              const tier = confidenceTier(insight.confidence);
              const ItemIcon = insightIcon(insight);
              return (
                <motion.div
                  key={insight.insight_id ?? `${insight.title}-${index}`}
                  className={`timeline-item${major ? " timeline-item--major" : ""}`}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.25, delay: Math.min(index, 6) * 0.03 }}
                >
                  <span className="timeline-time">
                    <ItemIcon size={13} aria-hidden="true" /> {formatMs(insight.provenance?.window?.end_ms)}
                  </span>
                  <div className="timeline-body">
                    <h4>{insight.title}</h4>
                    <p>{insight.summary}</p>
                    <div className="timeline-tags">
                      {major ? <span className="tag tag--turning">Turning point</span> : null}
                      <span className={`tag tag--conf-${tier}`}>{Math.round(insight.confidence * 100)}%</span>
                      {insight.provenance?.reason_code ? <span className="tag">{insight.provenance.reason_code}</span> : null}
                      {insight.clip?.status === "ready" ? <span className="tag">clip ready</span> : null}
                    </div>
                    {insight.clip?.status === "ready" ? (
                      <div className="timeline-actions">
                        <video
                          controls
                          preload="metadata"
                          className="clip-player"
                          src={clipUrlForInsight(insight) ?? undefined}
                        />
                      </div>
                    ) : null}
                  </div>
                </motion.div>
              );
            })}
          </div>
        )}
      </section>

      {/* ============ RECAP STORY ============ */}
      <section className="section" aria-label="Match recap">
        <div className="section-head">
          <span className="section-title">Full-time story</span>
          <div className="btn-row" style={{ display: "flex", gap: 8 }}>
            <button className="btn btn--ghost btn--sm" onClick={generateNarrative}>
              Generate narrative
            </button>
            <button className="btn btn--accent btn--sm" onClick={generateRecap}>
              Generate recap
            </button>
          </div>
        </div>

        <div className="story">
          {narrative ? (
            <div className="story-lede">
              {narrativeMeta ? <p className="chapter-meta" style={{ marginBottom: 6 }}>{narrativeMeta}</p> : null}
              <p>{narrative}</p>
            </div>
          ) : null}

          {recap ? (
            <div className="story-lede">
              {recapMeta ? <p className="chapter-meta" style={{ marginBottom: 6 }}>{recapMeta}</p> : null}
              <p>{recap}</p>
            </div>
          ) : null}

          {!narrative && !recap ? (
            <div className="empty-state">
              No story yet — generate a narrative mid-match or a full recap once the game concludes.
            </div>
          ) : null}

          {recapChapters.length > 0 ? (
            <div className="story-chapters">
              {recapChapters.map((chapter) => (
                <div key={chapter.chapter_id} className="story-chapter">
                  <h4>{chapter.title}</h4>
                  <p>{chapter.summary}</p>
                  <span className="chapter-meta">
                    {formatMs(chapter.window_start_ms)}–{formatMs(chapter.window_end_ms)} •{" "}
                    {Math.round(chapter.confidence * 100)}% • {chapter.provenance?.reason_code ?? "n/a"}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      {insights.length > 0 ? (
        <section className="section" aria-label="Export results">
          <div className="section-head">
            <span className="section-title">Export results</span>
            <span className="section-hint">Take the match intelligence with you</span>
          </div>
          <div className="export-row">
            <button className="btn btn--ghost" onClick={exportInsightsJson}>
              <Download size={14} aria-hidden="true" /> Insights JSON
            </button>
            <button className="btn btn--ghost" onClick={exportInsightsCsv}>
              <Download size={14} aria-hidden="true" /> Insights CSV
            </button>
          </div>
        </section>
      ) : null}

      <p className="footer-note">
        Tactiq explains the match — it never invents it. Built for the Premier League × Microsoft hackathon.
      </p>

      {/* ============ EVIDENCE PANEL (inline, no sidebar) ============ */}
      <section className="section" aria-label="Insight evidence">
        <div className="section-head">
          <span className="section-title">Why this matters</span>
          <span className="section-hint">Evidence-first explanation for the current insight</span>
        </div>
        <div className="inline-evidence">
          {heroInsight ? (
            <>
              <p className="evidence-note">
                Tactiq isn&apos;t guessing — this explanation is derived from measured match signals in a defined time window.
              </p>
              <div className="evidence-block">
                <h3>Insight</h3>
                <p style={{ fontSize: 14, color: "var(--text-primary)", marginBottom: 4 }}>{heroInsight.title}</p>
                <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>{heroInsight.summary}</p>
              </div>
              <div className="evidence-block">
                <h3>Confidence &amp; window</h3>
                <div className="evidence-grid">
                  <div className="evidence-cell">
                    <span className="metric">Confidence</span>
                    <span className="value">{Math.round(heroInsight.confidence * 100)}%</span>
                  </div>
                  {heroInsight.provenance?.window ? (
                    <div className="evidence-cell">
                      <span className="metric">Window</span>
                      <span className="value">
                        {formatMs(heroInsight.provenance.window.start_ms)}–{formatMs(heroInsight.provenance.window.end_ms)}
                      </span>
                    </div>
                  ) : null}
                  {heroInsight.provenance?.reason_code ? (
                    <div className="evidence-cell">
                      <span className="metric">Reason code</span>
                      <span className="value" style={{ fontSize: 12 }}>{heroInsight.provenance.reason_code}</span>
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="evidence-block">
                <h3>Supporting metrics</h3>
                <div className="evidence-grid">
                  {heroInsight.evidence.map((item) => (
                    <div className="evidence-cell" key={item.metric}>
                      <span className="metric">{item.metric}</span>
                      <span className="value">{item.value}</span>
                    </div>
                  ))}
                  {(heroInsight.provenance?.metrics ?? []).map((metric) => (
                    <div className="evidence-cell" key={`prov-${metric.metric}`}>
                      <span className="metric">{metric.metric}</span>
                      <span className="value">
                        {metric.value}
                        {metric.unit ? ` ${metric.unit}` : ""}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">No insight selected yet.</div>
          )}
        </div>
      </section>

      {/* ============ CONTROL CENTER (inline, no sidebar) ============ */}
      <section className="section" aria-label="Control center">
        <div className="section-head">
          <span className="section-title">Get started</span>
          <span className="section-hint">Simple first actions, advanced controls only when you need them</span>
        </div>
        <div className="quick-grid">
          <div className="quick-card">
            <span className="field-label">Quick demo</span>
            <div className="field-row">
              <label htmlFor="scenario">Scenario</label>
              <select id="scenario" value={selectedScenario} onChange={(event) => setSelectedScenario(event.target.value)}>
                <option value="sustained_pressure">sustained pressure</option>
                <option value="rhythm_shift">rhythm shift</option>
                <option value="player_influence">player influence</option>
              </select>
            </div>
            <button className="btn btn--primary btn--block" onClick={startDemo}>
              <Play size={14} aria-hidden="true" /> Start live scenario
            </button>
            {scenarios.length > 0 ? <p className="status-line">{scenarios.length} scenario templates ready</p> : null}
          </div>

          <div className="quick-card">
            <span className="field-label">Analyze your match video</span>
            <div className="field-row">
              <label htmlFor="video-file">Upload video file</label>
              <input
                id="video-file"
                type="file"
                accept="video/*"
                onChange={(event) => setSelectedVideoFile(event.target.files?.[0] ?? null)}
              />
            </div>
            <button className="btn btn--ghost btn--block" onClick={uploadVideo}>
              Upload and analyze
            </button>
            <div className="field-row">
              <label htmlFor="video-url">Or use direct video link</label>
              <input
                id="video-url"
                type="url"
                placeholder="https://example.com/video.mp4"
                value={videoUrl}
                onChange={(event) => setVideoUrl(event.target.value)}
              />
            </div>
            <button className="btn btn--ghost btn--block" onClick={ingestVideoFromLink}>
              Ingest from link
            </button>
            <p className="status-line">video: {videoStatus}</p>
            {videoSummary ? (
              <p className="status-line">
                {videoSummary.event_count} events • {videoSummary.insight_count} insights • {videoSummary.clips_generated ?? 0} clips
              </p>
            ) : null}
          </div>
        </div>

        <details className="advanced-panel">
          <summary>Advanced controls</summary>
          <div className="advanced-grid">
            <div className="sheet-section">
              <span className="field-label">Commentary</span>
              <div className="toggle-row">
                <label htmlFor="live-commentary">
                  <input
                    id="live-commentary"
                    type="checkbox"
                    checked={liveCommentaryEnabled}
                    onChange={(event) => setLiveCommentaryEnabled(event.target.checked)}
                  />
                  Live commentary
                </label>
                <label htmlFor="dual-commentary">
                  <input
                    id="dual-commentary"
                    type="checkbox"
                    checked={dualCommentaryEnabled}
                    onChange={(event) => setDualCommentaryEnabled(event.target.checked)}
                  />
                  Dual commentary
                </label>
              </div>
              <p className="status-line">speech: {speechStatus}</p>
            </div>

            <div className="sheet-section">
              <span className="field-label">Audit</span>
              <button className="btn btn--ghost btn--block" onClick={() => fetchCommentaryAudit()} disabled={!matchId}>
                Refresh commentary audit
              </button>
              <p className="status-line">audit: {commentaryAuditStatus}</p>
              {commentaryAudit.length > 0 ? (
                <details>
                  <summary>Latest orchestration calls ({commentaryAudit.length})</summary>
                  {commentaryAudit.map((entry) => (
                    <div key={entry.call_id} className="audit-entry">
                      <span className="who">
                        {entry.provider} {entry.fallback_used ? "• fallback" : "• primary path"}
                      </span>
                      <p className="status-line">
                        path={entry.audit?.orchestration_path ?? "n/a"} • tuning={entry.audit?.tuning_version ?? "n/a"}
                      </p>
                      <p style={{ fontSize: 12.5 }}>
                        <strong>Lead:</strong> {entry.primary_text}
                      </p>
                      <p style={{ fontSize: 12.5 }}>
                        <strong>Analyst:</strong> {entry.secondary_text}
                      </p>
                      {(entry.audit?.agents ?? []).map((agent) => (
                        <div key={`${entry.call_id}-${agent.name}-${agent.version ?? "none"}`}>
                          <p className="status-line">
                            agent={agent.name} • v{agent.version ?? "n/a"} • {agent.source ?? "n/a"}
                          </p>
                          {agent.instructions ? <p className="agent-instructions">{agent.instructions}</p> : null}
                        </div>
                      ))}
                    </div>
                  ))}
                </details>
              ) : null}
            </div>
          </div>
        </details>
      </section>
    </div>
  );
}
