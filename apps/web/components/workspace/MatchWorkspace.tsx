"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useReducedMotion } from "framer-motion";
import Link from "next/link";
import { API_BASE, apiFetch as fetch, responseError } from "../../lib/api";
import type { MatchStatePayload } from "../../lib/match-types";
import type { ServiceCapabilities } from "../../lib/service-capabilities";
import TacticalReplay from "../TacticalReplay";
import SessionAccess, { type WorkspaceAccess } from "../SessionAccess";
import OperationsPanel from "../OperationsPanel";
import Brand from "../Brand";
import PitchIllustration from "../landing/PitchIllustration";
import EntryPanel, { type EntryTab } from "./EntryPanel";
import MomentCard from "./MomentCard";
import SelectedMoment from "./SelectedMoment";
import WorkspacePreferences from "./WorkspacePreferences";
import CommentaryControls from "./CommentaryControls";
import { readableLabel, supportedMoments, teamMonogram, type Insight } from "../../lib/workspace-types";
import { readWorkspaceReference, saveWorkspaceReference, WORKSPACE_REFERENCE_KEY } from "../../lib/workspace-session";
import { AnalysisOperations, type AnalysisKind } from "../../lib/analysis-operation";
import { mayRead, mayAnalyze, mayInspect, mayStartAnalysis, retryAfterDeadline, sessionTime } from "../../lib/workspace-access";
import { automaticAudioIsCurrent, automaticMoment, momentAt, type CommentaryScript, type CommentaryTurn, type PlaybackInteraction,
  type PlaybackSnapshot, type ReplayControls } from "../../lib/playback-commentary";
import {
  ArrowLeft,
  ArrowUpRight,
  Download,
  Loader2,
  Plus,
  Radio,
  Search,
  Sparkles,
  Wifi,
  WifiOff,
} from "lucide-react";

type Scenario = {
  scenario_id: string;
  total_ms: number;
};

type EventPayload = {
  sequence?: number;
  team_id?: string;
  type?: string;
  position?: { x?: number; y?: number };
  end_position?: { x?: number; y?: number };
  qualifiers?: Record<string, unknown>;
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
  football_action_count?: number;
  tracking_sample_count?: number;
  insight_count: number;
  clips_generated?: number;
  state?: MatchStatePayload;
  insights: Insight[];
};

type AnalysisJob = {
  job_id: string;
  match_id?: string;
  status: "queued" | "running" | "succeeded" | "failed" | "dead_letter" | "cancelled";
  cancel_requested?: boolean;
  result?: VideoIngestionResponse | null;
  error?: string | null;
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

const DEFAULT_PRIMARY_VOICE = process.env.NEXT_PUBLIC_DEFAULT_PRIMARY_VOICE;
const DEFAULT_SECONDARY_VOICE = process.env.NEXT_PUBLIC_DEFAULT_SECONDARY_VOICE;
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
  const compatible = (voice: string | undefined) => voice
    && (voice.toLowerCase().startsWith(language.split("-")[0].toLowerCase() + "-") || voice.toLowerCase().includes("multilingual"))
    ? voice : undefined;
  return {
    primary: byLanguage.primary ?? compatible(DEFAULT_PRIMARY_VOICE),
    secondary: byLanguage.secondary ?? compatible(DEFAULT_SECONDARY_VOICE),
  };
}

function browserVoicePair(language: string): { primary: SpeechSynthesisVoice | null; secondary: SpeechSynthesisVoice | null } {
  if (typeof window === "undefined" || !window.speechSynthesis) {
    return { primary: null, secondary: null };
  }

  const voices = window.speechSynthesis.getVoices();
  const localeVoices = voices.filter((v) => v.lang?.toLowerCase().startsWith(language.toLowerCase().split("-")[0]));
  const sourceVoices = localeVoices;
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
  const sourceVoices = localeVoices;
  const maleVoices = sourceVoices.filter((voice) => voiceHasAnyHint(voice, MALE_HINTS));

  if (maleVoices.length < 2) {
    return { primary: null, secondary: null };
  }

  const primary = maleVoices[0] ?? null;
  const secondary = maleVoices.find((voice) => voice.voiceURI !== primary?.voiceURI) ?? null;
  return { primary, secondary };
}

export default function MatchWorkspace({ initialIntent = "demo", freshStart = false }: {
  initialIntent?: "demo" | "video"; freshStart?: boolean;
}) {
  const [access, setAccess] = useState<WorkspaceAccess>({ status: "checking", principal: null });
  const [entryTab, setEntryTab] = useState<EntryTab>(initialIntent);
  const [showEntry, setShowEntry] = useState(true);
  const [section, setSection] = useState<"match" | "moments" | "story">("match");
  const [navigationNotice, setNavigationNotice] = useState("");
  const [submittingVideo, setSubmittingVideo] = useState(false);
  const [recapBusy, setRecapBusy] = useState(false);
  const [seekRequest, setSeekRequest] = useState<{ timeMs: number; serial: number } | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedScenario, setSelectedScenario] = useState<string>("sustained_pressure");
  const [matchId, setMatchId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("idle");
  const [eventsCount, setEventsCount] = useState<number>(0);
  const [liveState, setLiveState] = useState<MatchStatePayload | null>(null);
  const [latestEvent, setLatestEvent] = useState<EventPayload | null>(null);
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
  const [capabilities, setCapabilities] = useState<ServiceCapabilities | null>(null);
  const [audioNotice, setAudioNotice] = useState("");
  const [transcript, setTranscript] = useState<CommentaryScript | null>(null);
  const [playback, setPlayback] = useState<PlaybackSnapshot | null>(null);
  const [videoStatus, setVideoStatus] = useState<string>("idle");
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [selectedVideoFile, setSelectedVideoFile] = useState<File | null>(null);
  const [analysisMode, setAnalysisMode] = useState<"events" | "cv">("cv");
  const [rightsConfirmed, setRightsConfirmed] = useState(false);
  const [cvOptionsText, setCvOptionsText] = useState("");
  const [teamAName, setTeamAName] = useState("");
  const [teamBName, setTeamBName] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [savedMatches, setSavedMatches] = useState<Array<{ match_id: string; updated_at: string }>>([]);
  const [sessionRevision, setSessionRevision] = useState(0);
  const [signInRequired, setSignInRequired] = useState(false);
  const [analysisPausedUntil, setAnalysisPausedUntil] = useState(0);
  const [savedNextAfter, setSavedNextAfter] = useState<string | null>(null);
  const [narrativeTitle, setNarrativeTitle] = useState("");
  const [videoSummary, setVideoSummary] = useState<VideoIngestionResponse | null>(null);
  const [liveCommentaryEnabled, setLiveCommentaryEnabled] = useState<boolean>(true);
  const [dualCommentaryEnabled, setDualCommentaryEnabled] = useState<boolean>(true);
  const [commentaryAudit, setCommentaryAudit] = useState<CommentaryAuditEntry[]>([]);
  const [commentaryAuditStatus, setCommentaryAuditStatus] = useState<string>("idle");
  const [timelineFilter, setTimelineFilter] = useState<string>("all");
  const [timelineSearch, setTimelineSearch] = useState<string>("");
  const accessRef = useRef(access);
  accessRef.current = access;
  const jobRef = useRef(jobId);
  const analysisOperationsRef = useRef<AnalysisOperations | null>(null);
  const analysisSubmittingRef = useRef(false);
  jobRef.current = jobId;
  const restoredOwnerRef = useRef<string | null>(null);
  const pinnedInsightRef = useRef(false);
  const wsRef = useRef<WebSocket | null>(null);
  const speakingRef = useRef<boolean>(false);
  const speechModeRef = useRef<"auto" | "manual" | null>(null);
  const speechAbortRef = useRef<AbortController | null>(null);
  const replayRef = useRef<ReplayControls>(null);
  const playbackRef = useRef<PlaybackSnapshot | null>(null);
  const autoBlockedIntentRef = useRef(-1);
  const lastAutoTimeRef = useRef(-Infinity);
  const spokenMomentsRef = useRef(new Set<string>());
  const capabilitiesRef = useRef(capabilities);
  capabilitiesRef.current = capabilities;
  const cloudSpeechUnavailableRef = useRef<boolean>(false);
  const activeMatchRef = useRef<string | null>(null);
  const auditRequestSequenceRef = useRef<number>(0);
  const narrativeRequestRef = useRef(0);
  const workspaceGenerationRef = useRef(0);
  const narrativeInsightRef = useRef<string | undefined>(undefined);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speechGenerationRef = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const cancelPlaybackRef = useRef<(() => void) | null>(null);
  const preferencesRef = useRef({ language, audienceMode, selectedPlayer, liveCommentaryEnabled, dualCommentaryEnabled });
  preferencesRef.current = { language, audienceMode, selectedPlayer, liveCommentaryEnabled, dualCommentaryEnabled };

  const wsUrl = useMemo(() => (API_BASE ? API_BASE.replace("http", "ws") : ""), []);
  const canRead = mayRead(access);
  const canAnalyze = mayAnalyze(access);
  const canStartAnalysis = mayStartAnalysis(access, capabilities?.analysis_admission_enabled, analysisPausedUntil);
  const canInspect = mayInspect(access);
  const isCloud = Boolean(access.cloud);

  function operations() {
    if (!analysisOperationsRef.current) {
      let storage: Storage | undefined;
      try { storage = window.sessionStorage; } catch { /* In-memory retries remain available. */ }
      analysisOperationsRef.current = new AnalysisOperations(storage);
    }
    return analysisOperationsRef.current;
  }

  async function analysisHeaders(kind: AnalysisKind, input: unknown): Promise<Record<string, string>> {
    if (!accessRef.current.cloud) return {};
    const principal = accessRef.current.principal;
    return { "Idempotency-Key": await operations().begin(kind, JSON.stringify([principal?.tenant_id, principal?.subject]), input) };
  }

  async function analysisError(response: Response): Promise<string> {
    if (accessRef.current.cloud && response.status === 403) {
      const payload = await response.clone().json().catch(() => null) as { detail?: { code?: string } } | null;
      if (payload?.detail?.code === "sign_in_required") {
        setSignInRequired(true);
        setSessionRevision((value) => value + 1);
      }
    }
    if (accessRef.current.cloud && (response.status === 429 || response.status === 503)) {
      const deadline = retryAfterDeadline(response.headers.get("Retry-After"));
      const payload = await response.clone().json().catch(() => null) as {
        detail?: { code?: string; trial_use_charged?: boolean };
      } | null;
      const capacityRejected = payload?.detail?.trial_use_charged === false
        && ["processing_allocation_unavailable", "processing_limit_exhausted", "analysis_capacity"].includes(payload.detail.code ?? "");
      setAnalysisPausedUntil(deadline);
      setSessionRevision((value) => value + 1);
      return `${await responseError(response)}${capacityRejected ? " No trial use was charged." : ""} New analyses are temporarily paused. Retry after ${sessionTime(deadline / 1000)}. Your existing matches remain available.`;
    }
    return responseError(response);
  }

  useEffect(() => {
    if (!analysisPausedUntil) return;
    const timer = setTimeout(() => {
      setAnalysisPausedUntil(0);
      setSessionRevision((value) => value + 1);
    }, Math.min(Math.max(0, analysisPausedUntil - Date.now()), 2_147_483_647));
    return () => clearTimeout(timer);
  }, [analysisPausedUntil]);

  async function loadMoreSaved() {
    if (!savedNextAfter) return;
    const generation = workspaceGenerationRef.current;
    try {
      const response = await fetch(`/api/v1/matches?after=${encodeURIComponent(savedNextAfter)}`);
      if (!response.ok) throw new Error(await responseError(response));
      const payload = await response.json() as { matches: Array<{ match_id: string; state: { updated_at?: string } }>; next_after: string | null };
      if (generation !== workspaceGenerationRef.current) return;
      setSavedMatches((previous) => [...previous, ...payload.matches.map((match) => ({ match_id: match.match_id, updated_at: match.state.updated_at ?? "" }))]
        .filter((match, index, matches) => matches.findIndex((entry) => entry.match_id === match.match_id) === index));
      setSavedNextAfter(payload.next_after);
    } catch (error) {
      if (generation === workspaceGenerationRef.current) setNavigationNotice(error instanceof Error ? error.message : "Unable to load saved matches.");
    }
  }

  useEffect(() => {
    if (!canRead) return;
    const controller = new AbortController();
    void fetch("/api/v1/matches", { signal: controller.signal }).then(async (response) => {
      if (response.ok) {
        const payload = await response.json() as Array<{ match_id: string; updated_at: string }>
          | { matches: Array<{ match_id: string; state: { updated_at?: string } }>; next_after: string | null };
        if (!controller.signal.aborted) {
          setSavedMatches(Array.isArray(payload) ? payload : payload.matches.map((match) => ({ match_id: match.match_id, updated_at: match.state.updated_at ?? "" })));
          setSavedNextAfter(Array.isArray(payload) ? null : payload.next_after);
        }
      } else if (response.status !== 401) {
        setStatus(await responseError(response));
      }
    }).catch(() => {
      if (!controller.signal.aborted) setStatus("backend unavailable (check API URL/CORS)");
    });
    return () => controller.abort();
  }, [sessionRevision, canRead, access.principal?.tenant_id, access.principal?.subject]);

  useEffect(() => {
    if (!canRead || !access.principal) return;
    const identity = JSON.stringify([access.principal.tenant_id, access.principal.subject]);
    if (restoredOwnerRef.current === identity) return;
    restoredOwnerRef.current = identity;
    try {
      const previous = readWorkspaceReference(window.sessionStorage, access.principal);
      if (previous?.jobId) setJobId(previous.jobId);
      else if (previous?.matchId && !freshStart) void loadSavedMatch(previous.matchId);
    } catch (error) {
      setNavigationNotice(error instanceof Error ? error.message : "The previous workspace could not be restored.");
    }
  }, [canRead, access.principal, freshStart]);

  useEffect(() => {
    if (!canRead || !access.principal || (!matchId && !jobId)) return;
    try {
      saveWorkspaceReference(window.sessionStorage, { owner: access.principal, matchId, jobId });
    } catch (error) {
      setNavigationNotice(error instanceof Error ? error.message : "Workspace navigation could not be remembered.");
    }
  }, [canRead, access.principal, matchId, jobId]);

  useEffect(() => {
    if (matchId) setShowEntry(false);
  }, [matchId]);

  useEffect(() => {
    if (!isCloud || !matchId || !["live", "running", "queued"].includes(status)) return;
    const timer = setInterval(() => void resyncMatch(matchId), 2000);
    return () => clearInterval(timer);
  }, [isCloud, matchId, status]);

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const response = await fetch(`/api/v1/jobs/${jobId}`, { signal: controller.signal });
        if ([401, 403, 404].includes(response.status)) {
          setVideoStatus("This analysis is no longer available to your current workspace.");
          setJobId(null);
          return;
        }
        if (!response.ok) throw new Error(await responseError(response));
        const job = await response.json() as AnalysisJob;
        if (controller.signal.aborted) return;
        setVideoStatus(job.cancel_requested ? "cancelling after the current processing step..." : `analysis ${job.status}`);
        if (job.status === "succeeded" && (job.result || job.match_id)) {
          if (accessRef.current.cloud && job.match_id) {
            await loadSavedMatch(job.match_id);
            if (controller.signal.aborted) return;
            setVideoStatus("Analysis complete.");
          } else if (job.result) {
            applyVideoSummary(job.result);
            setVideoStatus(`Analysis complete · ${job.result.football_action_count ?? job.result.state?.action_count ?? job.result.event_count} football actions · ${job.result.tracking_sample_count ?? job.result.state?.tracking_sample_count ?? 0} tracking samples.`);
          }
          setJobId(null);
          setSessionRevision((value) => value + 1);
        } else if (["failed", "dead_letter", "cancelled"].includes(job.status)) {
          setVideoStatus(job.error || `analysis ${job.status}`);
          setJobId(null);
        } else timer = setTimeout(() => void poll(), 1000);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setVideoStatus(reason instanceof Error ? reason.message : "Unable to retrieve job status.");
          timer = setTimeout(() => void poll(), 3000);
        }
      }
    }
    void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [jobId]);

  useEffect(() => {
    stopSpeech(false);
    spokenMomentsRef.current.clear();
    lastAutoTimeRef.current = -Infinity;
    autoBlockedIntentRef.current = -1;
    setTranscript(null);
    cloudSpeechUnavailableRef.current = false;
    setRecap("");
    setRecapMeta("");
    setRecapChapters([]);
    setNarrative("");
    setNarrativeMeta("");
    setNarrativeTitle("");
  }, [matchId, language, audienceMode, selectedPlayer, dualCommentaryEnabled]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      if (matchId && latestInsight) void generateNarrative(controller.signal, false);
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [matchId, latestInsight?.insight_id, language, audienceMode, selectedPlayer]);

  useEffect(() => {
    if (!liveCommentaryEnabled && speechModeRef.current === "auto") stopSpeech(false);
  }, [liveCommentaryEnabled]);

  useEffect(() => () => {
    const currentAccess = accessRef.current;
    if (mayRead(currentAccess) && currentAccess.principal) {
      try {
        saveWorkspaceReference(window.sessionStorage, {
          owner: currentAccess.principal, matchId: activeMatchRef.current, jobId: jobRef.current,
        });
      } catch (error) {
        console.warn("Workspace navigation could not be remembered.", error instanceof Error ? error.name : "StorageError");
      }
    }
    workspaceGenerationRef.current += 1;
    narrativeRequestRef.current += 1;
    auditRequestSequenceRef.current += 1;
    activeMatchRef.current = null;
    wsRef.current?.close();
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    stopSpeech();
  }, []);

  async function fetchPlaybackScript(
    effectiveMatchId: string, insightId: string | undefined, depth: "broadcast" | "deep",
    atMs: number | undefined, overview: boolean, signal: AbortSignal,
  ): Promise<CommentaryScript> {
    const language = preferencesRef.current.language;
    const preferences = preferencesRef.current;
    const dual = preferences.dualCommentaryEnabled;
    const response = await fetch(`/api/v1/matches/${effectiveMatchId}/${dual ? "commentary/dual" : "narratives"}`, {
      method: "POST", signal, headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language, insight_id: insightId ?? null, depth, at_ms: atMs, overview,
        ...(!dual ? { audience_mode: preferences.audienceMode,
          selected_player_id: preferences.audienceMode === "player" ? preferences.selectedPlayer : null } : {}) }),
    });
    if (!response.ok) throw new Error(await responseError(response));
    const payload = await response.json() as { turns?: CommentaryTurn[]; primary_text?: string; secondary_text?: string;
      body?: string; provider: string; fallback_used: boolean; script_id?: string;
      provider_failure?: CommentaryScript["provider_failure"] };
    const turns = dual ? payload.turns?.length ? payload.turns : [
      { role: "lead" as const, text: payload.primary_text ?? "" },
      { role: "analyst" as const, text: payload.secondary_text ?? "" },
    ] : [{ role: "lead" as const, text: payload.body ?? "" }];
    if (!Array.isArray(turns) || typeof payload.provider !== "string" || !turns.length
        || turns.some((turn, index) => typeof turn.text !== "string" || !turn.text.trim()
          || turn.role !== (dual && index % 2 ? "analyst" : "lead"))) {
      throw new Error("The commentary response did not contain a usable script.");
    }
    if (dual && !signal.aborted) void fetchCommentaryAudit(effectiveMatchId);
    return { turns, provider: payload.provider, fallback_used: payload.fallback_used, depth,
      script_id: payload.script_id, provider_failure: payload.provider_failure };
  }

  async function fetchCommentaryAudit(targetMatchId?: string) {
    if (!mayInspect(accessRef.current)) return;
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
    if (!canStartAnalysis || jobId || submittingVideo || analysisSubmittingRef.current) return;
    if (Boolean(teamAName.trim()) !== Boolean(teamBName.trim())) {
      setStatus("Enter both team names, or leave both names empty.");
      return;
    }
    pinnedInsightRef.current = false;
    playbackRef.current = null;
    setPlayback(null);
    setSeekRequest(null);
    setSection("match");
    if (!API_BASE) {
      setStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    analysisSubmittingRef.current = true;
    const workspaceGeneration = ++workspaceGenerationRef.current;
    activeMatchRef.current = null;
    setMatchId(null);
    wsRef.current?.close();
    wsRef.current = null;
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    stopSpeech();
    setStatus("creating match...");
    setInsights([]);
    setLatestInsight(null);
    setEventsCount(0);
    setLiveState(null);
    setLatestEvent(null);
    setNarrative("");
    setNarrativeMeta("");
    setRecap("");
    setRecapMeta("");
    setRecapChapters([]);
    setVideoSummary(null);
    setCommentaryAudit([]);
    setCommentaryAuditStatus("idle");
    auditRequestSequenceRef.current += 1;
    narrativeRequestRef.current += 1;
    setTimelineFilter("all");
    setTimelineSearch("");

    try {
      if (scenarios.length === 0) {
        const scenariosResponse = await fetch(`${API_BASE}/api/v1/scenarios`);
        if (!scenariosResponse.ok) {
          setStatus(`scenarios unavailable (${scenariosResponse.status})`);
          return;
        }
        const available = (await scenariosResponse.json()) as Scenario[];
        if (workspaceGeneration !== workspaceGenerationRef.current) return;
        setScenarios(available);
      }

      const input = {
        scenario_id: selectedScenario, seed: 42001,
        ...(teamAName.trim() && teamBName.trim() ? { team_display_names: { team_a: teamAName.trim(), team_b: teamBName.trim() } } : {}),
      };
      const createResponse = await fetch(`${API_BASE}/api/v1/matches`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...await analysisHeaders("scenario", input) },
        body: JSON.stringify(input),
      });
      if (!createResponse.ok) {
        setStatus(await analysisError(createResponse));
        return;
      }
      const created = (await createResponse.json()) as { match_id: string; job_id?: string };
      if (workspaceGeneration !== workspaceGenerationRef.current) return;
      if (isCloud) {
        if (!created.job_id) throw new Error("The analysis response did not include a job.");
        operations().complete("scenario");
        setJobId(created.job_id);
        setVideoStatus("scenario analysis queued");
        setStatus("queued");
        setSessionRevision((value) => value + 1);
        return;
      }

      setMatchId(created.match_id);
      activeMatchRef.current = created.match_id;
      setStatus("starting match...");

      await connectWs(created.match_id);
      if (workspaceGeneration !== workspaceGenerationRef.current) return;
      const startResponse = await fetch(`${API_BASE}/api/v1/matches/${created.match_id}/start`, {
        method: "POST",
      });
      if (!startResponse.ok) {
        setStatus(`match start failed (${startResponse.status})`);
        return;
      }

      setSessionRevision((value) => value + 1);
    } catch (error) {
      if (workspaceGeneration === workspaceGenerationRef.current) setStatus(error instanceof Error ? error.message : "backend unavailable (check API URL/CORS)");
    } finally {
      analysisSubmittingRef.current = false;
    }
  }

  function applyVideoSummary(payload: VideoIngestionResponse) {
    playbackRef.current = null;
    setPlayback(null);
    setShowEntry(false);
    pinnedInsightRef.current = false;
    setSeekRequest({ timeMs: 0, serial: 1 });
    setSection("match");
    wsRef.current?.close();
    wsRef.current = null;
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    stopSpeech();
    setVideoSummary(payload);
    const supported = supportedMoments(payload.insights ?? []);
    setMatchId(payload.match_id);
    setStatus("completed");
    setEventsCount(payload.football_action_count ?? payload.state?.action_count ?? payload.event_count);
    setLiveState(payload.state ?? null);
    setLatestEvent(null);
    setInsights(supported);
    setLatestInsight(supported[supported.length - 1] ?? null);
    setNarrative("");
    setNarrativeMeta("");
    setRecap("");
    setRecapMeta("");
    setRecapChapters([]);
    setCommentaryAudit([]);
    setCommentaryAuditStatus("idle");
    auditRequestSequenceRef.current += 1;
    narrativeRequestRef.current += 1;
    setTimelineFilter("all");
    setTimelineSearch("");
    activeMatchRef.current = payload.match_id;
  }

  async function uploadVideo() {
    if (!canStartAnalysis || jobId || submittingVideo || analysisSubmittingRef.current) return;
    const workspaceGeneration = workspaceGenerationRef.current;
    if (!API_BASE) {
      setVideoStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    if (!selectedVideoFile) {
      setVideoStatus("select a video file first");
      return;
    }
    if (Boolean(teamAName.trim()) !== Boolean(teamBName.trim())) {
      setVideoStatus("Enter both team names, or leave both names empty.");
      return;
    }

    analysisSubmittingRef.current = true;
    setSubmittingVideo(true);
    try {
      if (cvOptionsText.trim()) JSON.parse(cvOptionsText);
      setVideoStatus("uploading for background analysis...");
      const form = new FormData();
      form.append("file", selectedVideoFile);
      form.append("analysis_mode", analysisMode);
      if (isCloud) {
        form.append("rights_confirmed", "true");
        form.append("options", cvOptionsText.trim() || "{}");
        if (teamAName.trim() && teamBName.trim()) form.append("team_display_names", JSON.stringify({ team_a: teamAName.trim(), team_b: teamBName.trim() }));
      } else {
        if (cvOptionsText.trim()) form.append("options_json", cvOptionsText);
        if (teamAName.trim()) form.append("team_a_name", teamAName.trim());
        if (teamBName.trim()) form.append("team_b_name", teamBName.trim());
      }
      const response = await fetch("/api/v1/video/jobs", { method: "POST", body: form,
        headers: await analysisHeaders("upload", { file: { name: selectedVideoFile.name, size: selectedVideoFile.size,
          type: selectedVideoFile.type, modified: selectedVideoFile.lastModified },
          analysisMode, options: cvOptionsText.trim(), teamA: teamAName.trim(), teamB: teamBName.trim() }) });
      if (!response.ok) throw new Error(await analysisError(response));
      const job = await response.json() as AnalysisJob;
      if (workspaceGeneration !== workspaceGenerationRef.current) return;
      setJobId(job.job_id);
      if (isCloud) { operations().complete("upload"); setSessionRevision((value) => value + 1); }
    } catch (reason) {
      if (workspaceGeneration === workspaceGenerationRef.current) setVideoStatus(reason instanceof Error ? reason.message : "Video upload failed.");
    } finally {
      if (workspaceGeneration === workspaceGenerationRef.current) { setSubmittingVideo(false); analysisSubmittingRef.current = false; }
    }
  }

  async function ingestVideoFromLink() {
    if (!canStartAnalysis || jobId || submittingVideo || analysisSubmittingRef.current) return;
    const workspaceGeneration = workspaceGenerationRef.current;
    if (!API_BASE) {
      setVideoStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return;
    }

    if (!videoUrl.trim()) {
      setVideoStatus("enter a video URL first");
      return;
    }
    if (Boolean(teamAName.trim()) !== Boolean(teamBName.trim())) {
      setVideoStatus("Enter both team names, or leave both names empty.");
      return;
    }

    analysisSubmittingRef.current = true;
    setSubmittingVideo(true);
    try {
      const options: unknown = cvOptionsText.trim() ? JSON.parse(cvOptionsText) : {};
      setVideoStatus("preparing the authorized video link...");
      const input = {
          url: videoUrl.trim(), analysis_mode: analysisMode, options, rights_confirmed: isCloud || rightsConfirmed,
          ...(teamAName.trim() && teamBName.trim()
            ? { team_display_names: { team_a: teamAName.trim(), team_b: teamBName.trim() } } : {}),
        };
      const response = await fetch("/api/v1/video/jobs/from-link", {
        method: "POST", headers: { "Content-Type": "application/json", ...await analysisHeaders("link", input) },
        body: JSON.stringify(input),
      });
      if (!response.ok) throw new Error(await analysisError(response));
      const job = await response.json() as AnalysisJob;
      if (workspaceGeneration !== workspaceGenerationRef.current) return;
      setJobId(job.job_id);
      setVideoStatus("video analysis queued");
      if (isCloud) { operations().complete("link"); setSessionRevision((value) => value + 1); }
    } catch (reason) {
      if (workspaceGeneration === workspaceGenerationRef.current) setVideoStatus(reason instanceof Error ? reason.message : "Video link ingestion failed.");
    } finally {
      if (workspaceGeneration === workspaceGenerationRef.current) { setSubmittingVideo(false); analysisSubmittingRef.current = false; }
    }
  }

  async function generateNarrative(signal?: AbortSignal, allowCloud = true) {
    if (!matchId || !API_BASE) {
      return;
    }

    const sequence = ++narrativeRequestRef.current;
    const targetMatch = matchId;
    try {
    const response = await fetch(`${API_BASE}/api/v1/matches/${matchId}/narratives`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        audience_mode: audienceMode,
        language,
        selected_player_id: audienceMode === "player" ? selectedPlayer : null,
        insight_id: latestInsight?.insight_id,
        allow_cloud: allowCloud, depth: "deep",
      }),
    });

    if (!response.ok) {
      if (sequence === narrativeRequestRef.current && !signal?.aborted) {
        setNarrativeMeta(await responseError(response));
      }
      return;
    }

    const payload = await response.json();
    if (sequence !== narrativeRequestRef.current || signal?.aborted || activeMatchRef.current !== targetMatch
        || preferencesRef.current.language !== language || preferencesRef.current.audienceMode !== audienceMode
        || preferencesRef.current.selectedPlayer !== selectedPlayer) return;
    setNarrative(payload.body);
    setNarrativeTitle(payload.title);
    narrativeInsightRef.current = latestInsight?.insight_id;
    setNarrativeMeta(
      `${payload.audience_mode} • ${payload.language} • provider=${payload.provider} • fallback=${payload.fallback_used}`,
    );
    } catch (reason) {
      if (!signal?.aborted && sequence === narrativeRequestRef.current) {
        setNarrativeMeta(reason instanceof Error ? reason.message : "Narrative unavailable.");
      }
    }
  }

  async function generateRecap() {
    if (!matchId || !API_BASE) {
      return;
    }

    const target = matchId;
    try {
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
      const detail = await responseError(response);
      if (activeMatchRef.current === target && preferencesRef.current.language === language
        && preferencesRef.current.audienceMode === audienceMode && preferencesRef.current.selectedPlayer === selectedPlayer) {
        setRecapMeta(detail);
        setRecapChapters([]);
      }
      return;
    }

    const payload = (await response.json()) as { body: string; audience_mode: string; language: string; provider: string; fallback_used: boolean; chapters?: RecapChapter[] };
    if (activeMatchRef.current !== target || preferencesRef.current.language !== payload.language
        || preferencesRef.current.audienceMode !== audienceMode || preferencesRef.current.selectedPlayer !== selectedPlayer) return;
    setRecap(payload.body);
    setRecapMeta(
      `${payload.audience_mode} • ${payload.language} • provider=${payload.provider} • fallback=${payload.fallback_used}`,
    );
    setRecapChapters(payload.chapters ?? []);
    } catch (error) {
      if (activeMatchRef.current === target && preferencesRef.current.language === language
        && preferencesRef.current.audienceMode === audienceMode && preferencesRef.current.selectedPlayer === selectedPlayer) {
        setRecapMeta(error instanceof Error ? error.message : "Recap unavailable.");
      }
    }
  }

  async function playSpeech(turns: CommentaryTurn[], dual: boolean, mode: "auto" | "manual", signal: AbortSignal,
    requestedAtMs = 0, scriptId?: string) {
    const primary = turns.map((turn) => turn.text).join(" ");
    const language = preferencesRef.current.language;
    const generation = speechGenerationRef.current;
    const timely = () => mode === "manual" || automaticAudioIsCurrent(playbackRef.current, requestedAtMs);
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

    const cloudConfigured = capabilitiesRef.current?.speech_configured ?? false;
    const localeConfigured = capabilitiesRef.current?.speech?.languages[language]?.[dual ? "dual" : "single"] ?? cloudConfigured;
    const ownedSpeech = accessRef.current.cloud?.owned_speech_required === true;
    if (CLOUD_SPEECH_ENABLED && cloudConfigured && localeConfigured && !cloudSpeechUnavailableRef.current) {
      setSpeechStatus("requesting azure speech...");
      try {
        if (ownedSpeech && (!scriptId || !activeMatchRef.current)) {
          throw new Error("This commentary has no verified owned script for Azure Speech.");
        }
        response = await fetch(dual ? `${API_BASE}/api/v1/speech/synthesize-dual` : `${API_BASE}/api/v1/speech/synthesize`, {
          method: "POST",
          signal,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(
            ownedSpeech ? { match_id: activeMatchRef.current, script_id: scriptId } : dual
              ? {
                  turns,
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
      } catch (error) {
        if (signal.aborted) return;
        cloudSpeechUnavailableRef.current = true;
        setAudioNotice(error instanceof Error ? `Cloud speech unavailable: ${error.message}` : "Cloud speech unavailable.");
      }
    }

    if (generation !== speechGenerationRef.current) return;
    if (response && !response.ok) {
      const detail = await responseError(response);
      if (generation !== speechGenerationRef.current) return;
      setAudioNotice(`Cloud speech unavailable: ${detail}`);
      if (response.status < 500) throw new Error(detail);
      cloudSpeechUnavailableRef.current = true;
    }
    if (response?.ok) {
      const payload = await response.json();
      if (generation !== speechGenerationRef.current) return;
      if (!timely()) { setSpeechStatus("Skipping late commentary to stay aligned. Listen remains available."); return; }
      const audio = new Audio(`data:${payload.content_type};base64,${payload.audio_base64}`);
      audio.playbackRate = mode === "auto" ? playbackRef.current?.rate ?? 1 : 1;
      audioRef.current = audio;
      await new Promise<void>((resolve, reject) => {
        const complete = () => {
          if (cancelPlaybackRef.current === complete) cancelPlaybackRef.current = null;
          if (audioRef.current === audio) {
            audioRef.current = null;
            replayRef.current?.duck(false);
          }
          resolve();
        };
        cancelPlaybackRef.current = complete;
        audio.onplay = () => {
          if (generation !== speechGenerationRef.current) return;
          replayRef.current?.duck(true);
          setSpeechStatus(`speaking via ${payload.provider}`);
        };
        audio.onended = () => {
          if (generation === speechGenerationRef.current) setSpeechStatus("done");
          complete();
        };
        audio.onerror = () => {
          if (generation !== speechGenerationRef.current) { complete(); return; }
          cancelPlaybackRef.current = null;
          setSpeechStatus("audio playback error");
          reject(new Error("audio playback error"));
        };
        void audio.play().catch((error: unknown) => {
          if (generation === speechGenerationRef.current) {
            setSpeechStatus("Playback was blocked or failed. Use Listen to retry.");
          }
          reject(error);
        });
      });
      return;
    }

    if (!response || response.status >= 500 || response.status === 0) {
      cloudSpeechUnavailableRef.current = true;
      setSpeechStatus("cloud speech unavailable; checking browser voices");
    }

    await getBrowserVoicesWithRetry();
    if (generation !== speechGenerationRef.current) return;
    if (!timely()) { setSpeechStatus("Skipping late commentary to stay aligned. Listen remains available."); return; }
    if (dual) {
      if (typeof window === "undefined" || !window.speechSynthesis) {
        setSpeechStatus("cloud dual speech unavailable");
        return;
      }

      const strictMalePair = browserStrictMaleVoicePair(language);
      if (generation !== speechGenerationRef.current) return;
      if (!strictMalePair.primary || !strictMalePair.secondary) {
        setSpeechStatus("Dual speech unavailable: configure Azure Speech or two distinct male browser voices for this language.");
        return;
      }

      window.speechSynthesis.cancel();
      const speakOnce = (text: string, voice: SpeechSynthesisVoice | null, rate: number, pitch: number) =>
        new Promise<void>((resolve, reject) => {
          const complete = () => { if (cancelPlaybackRef.current === complete) cancelPlaybackRef.current = null; resolve(); };
          cancelPlaybackRef.current = complete;
          const u = new SpeechSynthesisUtterance(text);
          u.lang = language;
          u.rate = rate * (mode === "auto" ? playbackRef.current?.rate ?? 1 : 1);
          u.pitch = pitch;
          if (voice) {
            u.voice = voice;
          }
          u.onstart = () => {
            if (generation !== speechGenerationRef.current) return;
            replayRef.current?.duck(true);
            setSpeechStatus(`speaking dual via browser male (${language}) · cloud fallback`);
          };
          u.onend = complete;
          u.onerror = () => reject(new Error("browser speech error"));
          window.speechSynthesis.speak(u);
        });

      try {
        for (const turn of turns) {
          if (generation !== speechGenerationRef.current) return;
          await speakOnce(turn.text, turn.role === "lead" ? strictMalePair.primary : strictMalePair.secondary,
            turn.role === "lead" ? 1.04 : .98, turn.role === "lead" ? .98 : .9);
        }
        if (generation === speechGenerationRef.current) setSpeechStatus("done");
      } catch {
        if (generation === speechGenerationRef.current) setSpeechStatus("browser speech error");
      } finally {
        if (generation === speechGenerationRef.current) replayRef.current?.duck(false);
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
    if (!browserPair.primary) {
      setSpeechStatus(`No browser voice for ${language}; configure Azure Speech for this language.`);
      return;
    }

    const speakOnce = (text: string, voice: SpeechSynthesisVoice | null, rate: number, pitch: number) =>
      new Promise<void>((resolve, reject) => {
        const complete = () => { if (cancelPlaybackRef.current === complete) cancelPlaybackRef.current = null; resolve(); };
        cancelPlaybackRef.current = complete;
        const u = new SpeechSynthesisUtterance(text);
        u.lang = language;
        u.rate = rate * (mode === "auto" ? playbackRef.current?.rate ?? 1 : 1);
        u.pitch = pitch;
        if (voice) {
          u.voice = voice;
        }
        u.onstart = () => {
          if (generation !== speechGenerationRef.current) return;
          replayRef.current?.duck(true);
          setSpeechStatus(`speaking via browser (${language}) · cloud fallback`);
        };
        u.onend = complete;
        u.onerror = () => reject(new Error("browser speech error"));
        window.speechSynthesis.speak(u);
      });

    try {
      await speakOnce(primary, browserPair.primary, 1.02, 1.03);
      if (generation === speechGenerationRef.current) setSpeechStatus("done");
    } catch {
      if (generation !== speechGenerationRef.current) return;
      setSpeechStatus("browser speech error");
      throw new Error("browser speech error");
    } finally {
      if (generation === speechGenerationRef.current) replayRef.current?.duck(false);
    }
  }

  async function requestCommentary(mode: "auto" | "manual", insight?: Insight, atMs?: number, prepared?: CommentaryScript) {
    const target = activeMatchRef.current;
    if (!target || !canRead || mode === "auto" && speakingRef.current) return;
    stopSpeech(false);
    if (mode === "manual") {
      cloudSpeechUnavailableRef.current = false;
      setAudioNotice("");
    }
    speechModeRef.current = mode;
    speakingRef.current = true;
    if (mode === "manual") replayRef.current?.pause();
    const generation = speechGenerationRef.current;
    const controller = new AbortController();
    speechAbortRef.current = controller;
    const depth = mode === "manual" ? "deep" : "broadcast";
    setSpeechStatus(mode === "manual" ? "preparing in-depth commentary..." : "preparing playback commentary...");
    try {
      const scripted = prepared ?? await fetchPlaybackScript(target, insight?.insight_id, depth, atMs, !insight?.insight_id, controller.signal);
      if (controller.signal.aborted || generation !== speechGenerationRef.current || activeMatchRef.current !== target) return;
      if (mode === "auto" && !automaticAudioIsCurrent(playbackRef.current, atMs ?? 0)) {
        setSpeechStatus("Skipping late commentary to stay aligned. Listen remains available.");
        return;
      }
      setTranscript(scripted);
      if (scripted.provider.startsWith("guardrail-") && mode === "auto") {
        setSpeechStatus("Automatic commentary is waiting for supported evidence.");
        return;
      }
      await playSpeech(scripted.turns, !prepared && preferencesRef.current.dualCommentaryEnabled, mode,
        controller.signal, atMs, scripted.script_id);
    } catch (error) {
      if (!controller.signal.aborted && generation === speechGenerationRef.current) {
        setSpeechStatus(error instanceof Error ? error.message : "Commentary unavailable.");
      }
    } finally {
      if (generation === speechGenerationRef.current) {
        speakingRef.current = false;
        speechModeRef.current = null;
        speechAbortRef.current = null;
        replayRef.current?.duck(false);
      }
    }
  }

  function speakCurrentText() {
    void requestCommentary("manual", latestInsight ?? undefined, latestInsight?.provenance?.window?.end_ms);
  }

  function onReplayInteraction(reason: PlaybackInteraction) {
    if (reason === "pause") {
      if (speechModeRef.current === "auto") stopSpeech(false);
      return;
    }
    if (reason === "play") {
      if (speechModeRef.current === "manual") stopSpeech(false);
      pinnedInsightRef.current = false;
      autoBlockedIntentRef.current = -1;
      return;
    }
    stopSpeech(false);
    spokenMomentsRef.current.clear();
    lastAutoTimeRef.current = -Infinity;
  }

  function onReplayPlayback(next: PlaybackSnapshot) {
    if (next.matchId !== activeMatchRef.current) return;
    playbackRef.current = next;
    setPlayback(next);
    if (!next.playing && speechModeRef.current === "auto") stopSpeech(false);
    if (!pinnedInsightRef.current && !next.followingCurrent) {
      const current = momentAt(insights, next.timeMs);
      setLatestInsight((previous) => previous?.insight_id === current?.insight_id ? previous : current);
    }
    if (!preferencesRef.current.liveCommentaryEnabled || pinnedInsightRef.current || speakingRef.current
        || next.intent <= autoBlockedIntentRef.current || next.rate < .5 || next.rate > 2) return;
    const candidate = automaticMoment(next, insights, lastAutoTimeRef.current, spokenMomentsRef.current);
    if (!candidate) return;
    if (!candidate.insight && next.frame?.tracking.tracking_source === "event_stream_v1") return;
    spokenMomentsRef.current.add(candidate.key);
    lastAutoTimeRef.current = next.timeMs;
    void requestCommentary("auto", candidate.insight, Math.floor(next.timeMs));
  }

  function stopSpeech(blockAuto = true) {
    speechGenerationRef.current += 1;
    speechAbortRef.current?.abort();
    speechAbortRef.current = null;
    if (blockAuto) autoBlockedIntentRef.current = playbackRef.current?.intent ?? 0;
    speakingRef.current = false;
    speechModeRef.current = null;
    const audio = audioRef.current;
    cancelPlaybackRef.current?.();
    cancelPlaybackRef.current = null;
    audio?.pause();
    audioRef.current = null;
    replayRef.current?.duck(false);
    if (typeof window !== "undefined") window.speechSynthesis?.cancel();
    setSpeechStatus("stopped");
  }

  async function loadSavedMatch(id: string) {
    setNavigationNotice("");
    const workspaceGeneration = ++workspaceGenerationRef.current;
    activeMatchRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    stopSpeech();
    setMatchId(null);
    setLiveState(null);
    setLatestInsight(null);
    setInsights([]);
    setStatus("restoring match...");
    try {
      const [stateResponse, insightResponse] = await Promise.all([
        fetch(`/api/v1/matches/${id}/state`), fetch(`/api/v1/matches/${id}/insights`),
      ]);
      if (!stateResponse.ok) throw new Error(await responseError(stateResponse));
      if (!insightResponse.ok) throw new Error(await responseError(insightResponse));
      const state = await stateResponse.json() as MatchStatePayload;
      const payload = await insightResponse.json() as Insight[] | { insights: Insight[] };
      const existing = supportedMoments(Array.isArray(payload) ? payload : payload.insights);
      if (workspaceGeneration !== workspaceGenerationRef.current) return;
      applyVideoSummary({
        match_id: id, source: "saved match", extractor: state.analysis_metadata?.detection_source ?? "synthetic",
        extractor_confidence: 0, notes: state.analysis_metadata?.notes ?? "",
        event_count: state.last_sequence ?? 0, insight_count: existing.length, state, insights: existing,
      });
      setStatus(state.status === "running" ? "live" : state.status ?? "ready");
      if (state.status === "running" && !accessRef.current.cloud) await connectWs(id);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to restore match.");
    }
  }

  async function cancelAnalysis() {
    if (!jobId) return;
    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response));
      setVideoStatus("cancellation requested");
    } catch (error) {
      setVideoStatus(error instanceof Error ? error.message : "Unable to cancel analysis.");
    }
  }

  function resetWorkspaceSession(clearPendingOperations = true) {
    try {
      window.sessionStorage.removeItem(WORKSPACE_REFERENCE_KEY);
    } catch (error) {
      console.warn("Workspace shortcut storage is unavailable.", error instanceof Error ? error.name : "StorageError");
    }
    restoredOwnerRef.current = null;
    if (clearPendingOperations) analysisOperationsRef.current?.clear();
    analysisSubmittingRef.current = false;
    jobRef.current = null;
    pinnedInsightRef.current = false;
    setSeekRequest(null);
    setShowEntry(true);
    setSection("match");
    setEntryTab(initialIntent);
    setSubmittingVideo(false);
    setRecapBusy(false);
    setNavigationNotice("");
    workspaceGenerationRef.current += 1;
    activeMatchRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    stopSpeech();
    narrativeRequestRef.current += 1;
    auditRequestSequenceRef.current += 1;
    setJobId(null);
    setMatchId(null);
    setLiveState(null);
    setLatestEvent(null);
    setInsights([]);
    setLatestInsight(null);
    setEventsCount(0);
    setVideoSummary(null);
    setSavedMatches([]);
    setSavedNextAfter(null);
    setSignInRequired(false);
    if (clearPendingOperations) setAnalysisPausedUntil(0);
    setNarrative("");
    setNarrativeTitle("");
    setNarrativeMeta("");
    setRecap("");
    setRecapMeta("");
    setRecapChapters([]);
    setCommentaryAudit([]);
    setTeamAName("");
    setTeamBName("");
    setVideoUrl("");
    setSelectedVideoFile(null);
    setCvOptionsText("");
    setAudienceMode("fan");
    setLanguage("en-GB");
    setSelectedPlayer("a_10");
    setTimelineFilter("all");
    setTimelineSearch("");
    setStatus("idle");
    document.querySelectorAll(".app-shell video").forEach((element) => {
      if (element instanceof HTMLMediaElement) {
        element.pause();
        element.removeAttribute("src");
        element.load();
      }
    });
    document.querySelectorAll<HTMLInputElement>(".app-shell input[type='file']").forEach((element) => {
      element.value = "";
    });
    setSessionRevision((value) => value + 1);
  }

  async function toggleMatchPause() {
    if (!matchId) return;
    try {
      if (status === "paused") await connectWs(matchId);
      const response = await fetch(`/api/v1/matches/${matchId}/${status === "paused" ? "start" : "pause"}`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response));
      setStatus(status === "paused" ? "live" : "paused");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to change match state.");
    }
  }

  async function resyncMatch(id: string) {
    try {
      const [stateResponse, insightResponse] = await Promise.all([
        fetch(`/api/v1/matches/${id}/state`), fetch(`/api/v1/matches/${id}/insights`),
      ]);
      if (!stateResponse.ok || !insightResponse.ok) throw new Error("Unable to resynchronize match.");
      const state = await stateResponse.json() as MatchStatePayload;
      const payload = await insightResponse.json() as Insight[] | { insights: Insight[] };
      const existing = supportedMoments(Array.isArray(payload) ? payload : payload.insights);
      if (activeMatchRef.current !== id) return;
      applyIncomingState(state);
      setInsights(existing);
      if (!pinnedInsightRef.current && (!playbackRef.current || playbackRef.current.followingCurrent)) {
        setLatestInsight(existing[existing.length - 1] ?? null);
      }
      if (state.status === "completed" || state.status === "failed") {
        const socket = wsRef.current;
        wsRef.current = null;
        socket?.close();
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Stream resynchronization failed.");
    }
  }

  function applyIncomingState(state: MatchStatePayload) {
    setLiveState(state);
    if (typeof state.action_count === "number") setEventsCount(state.action_count);
    else if (typeof state.last_sequence === "number") setEventsCount(state.last_sequence);
    if (state.status === "running") setStatus("live");
    else if (state.status === "completed" || state.status === "paused" || state.status === "failed") {
      setStatus(state.status);
    }
  }

  function connectWs(id: string, attempt = 0): Promise<void> {
    if (!wsUrl) {
      setStatus("missing NEXT_PUBLIC_API_BASE_URL");
      return Promise.reject(new Error("API URL unavailable"));
    }
    if (reconnectRef.current) clearTimeout(reconnectRef.current);
    wsRef.current?.close();
    const ws = new WebSocket(`${wsUrl}/api/v1/ws/matches/${id}`);
    wsRef.current = ws;
    let snapshotStatus: string | undefined;
    const connected = new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => { ws.close(); reject(new Error("Match stream connection timed out")); }, 10_000);
      ws.onopen = () => { clearTimeout(timeout); setStatus("live"); resolve(); };
      ws.onerror = () => { clearTimeout(timeout); reject(new Error("Match stream unavailable")); };
      ws.onclose = (event) => {
        clearTimeout(timeout);
        if (wsRef.current !== ws || activeMatchRef.current !== id) return;
        reject(new Error(`Match stream closed (${event.code})`));
        setStatus("disconnected");
        if (attempt < 5 && event.code !== 4401 && event.code !== 4403 && event.code !== 4404) {
          reconnectRef.current = setTimeout(() => {
            void connectWs(id, attempt + 1).catch((error: Error) => setStatus(error.message));
          }, Math.min(5000, 500 * 2 ** attempt));
        }
      };
    });

    ws.onmessage = (event) => {
      if (wsRef.current !== ws || activeMatchRef.current !== id) return;
      let message: { type: string; payload?: unknown };
      try {
        const decoded: unknown = JSON.parse(event.data);
        if (!decoded || typeof decoded !== "object" || !("type" in decoded) || typeof decoded.type !== "string") {
          throw new Error("Invalid stream envelope");
        }
        message = { type: decoded.type, payload: "payload" in decoded ? decoded.payload : undefined };
      } catch {
        setStatus("invalid match-stream message");
        return;
      }
      if (message.type === "resync.required") { void resyncMatch(id); return; }
      if (message.type === "insights.snapshot" && Array.isArray(message.payload)) {
        const existing = supportedMoments(message.payload as Insight[]);
        setInsights(existing);
        if (!pinnedInsightRef.current && (!playbackRef.current || playbackRef.current.followingCurrent)) {
          setLatestInsight(existing[existing.length - 1] ?? null);
        }
        if (snapshotStatus === "completed" || snapshotStatus === "failed") {
          wsRef.current = null;
          ws.close();
        }
      }

      if (message.type === "match.snapshot" && message.payload) {
        const snapshot = message.payload as MatchStatePayload;
        snapshotStatus = snapshot.status;
        applyIncomingState(snapshot);
      }

      if (message.type === "event.created") {
        if (message.payload) {
          setLatestEvent(message.payload as EventPayload);
        }
      }

      if (message.type === "state.updated" && message.payload) {
        const state = message.payload as MatchStatePayload;
        applyIncomingState(state);
      }

      if (message.type === "insight.created" && message.payload) {
        const next = message.payload as Insight;
        if (next.provenance?.quality?.eligible === false) return;
        if (!pinnedInsightRef.current && (!playbackRef.current || playbackRef.current.followingCurrent)) setLatestInsight(next);
        setInsights((existing) => existing.some((insight) => insight.insight_id === next.insight_id) ? existing : [...existing, next]);

      }

      if (message.type === "match.status" && message.payload && typeof message.payload === "object" && "status" in message.payload) {
        const next = String(message.payload.status);
        setStatus(next);
        if (["completed", "paused", "failed"].includes(next)) {
          wsRef.current = null;
          ws.close();
        }
      }
    };
    return connected;
  }

  function liveStatusMeta(value: string): { icon: typeof Wifi; label: string; tone: "on" | "done" | "error" | "idle" } {
    if (value === "live") return { icon: Radio, label: "Live", tone: "on" };
    if (value === "completed") return { icon: Wifi, label: "Full time", tone: "done" };
    if (value.includes("error") || value.includes("missing") || value === "disconnected") {
      return { icon: WifiOff, label: value, tone: "error" };
    }
    return { icon: Loader2, label: value || "idle", tone: "idle" };
  }

  function formatMs(value?: number): string {
    if (typeof value !== "number") return "—";
    const totalSeconds = Math.floor(value / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  }

  const teamNames = {
    team_a: liveState?.team_display_names?.team_a ?? "Team A",
    team_b: liveState?.team_display_names?.team_b ?? "Team B",
  };

  function teamLabel(teamId?: string | null): string {
    if (!teamId) return "Unknown";
    if (teamId === "team_a") return teamNames.team_a;
    if (teamId === "team_b") return teamNames.team_b;
    return teamId.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  const turningPoints = insights.filter((i) => i.type === "turning_point").length;
  const heroInsight = latestInsight;
  const playheadActive = playback && playback.matchId === matchId && !playback.followingCurrent;
  const liveCv = playheadActive ? playback.frame?.tracking : liveState?.cv_live;
  const displayedScore = playheadActive ? playback.frame?.score : liveState?.score;
  const displayedClock = playheadActive ? playback.timeMs : liveState?.match_clock_ms;
  const playerOptions = Object.values(liveState?.player_roster ?? {});
  const possessionTeamId = playheadActive ? playback.frame?.possession_team_id : liveState?.possession_team_id ?? liveCv?.possession_team_id ?? null;
  const trackedPlayers = liveCv?.tracked_players ?? 0;
  const ballSpeed = liveCv?.ball_speed_kmh;
  const ballDirection = liveCv?.ball_travel_direction;
  const topPlayerSpeed = liveCv?.top_player_speed_kmh;
  const teamIdConfidence = liveCv?.team_identification_confidence;
  const possessionTransitionConfidence = liveCv?.possession_transition_confidence;
  const controlA = liveCv?.control_index_by_team?.team_a;
  const controlB = liveCv?.control_index_by_team?.team_b;
  const controlDelta = liveCv?.control_delta;
  const controlLeader = liveCv?.controlling_team_id;
  const pitchZone = liveCv?.pitch_zone;
  const isVideoAnalysis = liveState?.scenario_id === "video_upload" || liveState?.scenario_id === "cv_upload";
  const source = liveState?.analysis_metadata?.detection_source ?? liveCv?.detection_source ?? "synthetic";
  const trackingQuality = liveState?.analysis_metadata?.tracking_quality;
  const overviewAvailable = source !== "heuristic" && Boolean(liveState?.tracking_sample_count || trackingQuality?.sampled_frames);
  const sourceLabel = source === "synthetic" ? (isVideoAnalysis ? "Synthetic preview" : "Simulated scenario")
    : source === "model" ? "Model tracking" : source === "heuristic" ? "Development motion preview" : readableLabel(source);
  const statusMeta = { ...liveStatusMeta(status),
    ...(isVideoAnalysis && status === "completed" ? { label: "Analysis complete" } : {}) };
  const StatusIcon = statusMeta.icon;
  const prefersReducedMotion = useReducedMotion();
  const isConnecting = status.includes("creating") || status.includes("starting") || status.includes("restoring");

  const avgConfidence =
    insights.length > 0 ? insights.reduce((sum, i) => sum + i.confidence, 0) / insights.length : 0;

  const typeCounts = insights.reduce<Record<string, number>>((acc, i) => {
    const key = i.type ?? "other";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
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

  function selectInsight(insight: Insight, reveal = true) {
    pinnedInsightRef.current = true;
    narrativeRequestRef.current += 1;
    stopSpeech();
    setLatestInsight(insight);
    setNarrative("");
    setNarrativeTitle("");
    setNarrativeMeta("");
    setNavigationNotice("");
    if (typeof insight.provenance?.window?.end_ms === "number") {
      setSeekRequest((previous) => ({ timeMs: insight.provenance!.window!.end_ms!, serial: (previous?.serial ?? 0) + 1 }));
    }
    if (reveal) {
      setSection("match");
      requestAnimationFrame(() => {
        document.getElementById("workspace-tab-match")?.focus({ preventScroll: true });
        document.getElementById("match-stage")?.scrollIntoView({ block: "start", behavior: prefersReducedMotion ? "instant" : "smooth" });
      });
    }
  }

  function selectInsightById(id: string) {
    const selected = insights.find((insight) => insight.insight_id === id);
    if (selected) selectInsight(selected, false);
    else setNavigationNotice("That moment is still loading. Select it again once the match has synchronized.");
  }

  function followLatestInsight() {
    pinnedInsightRef.current = false;
    narrativeRequestRef.current += 1;
    stopSpeech();
    setLatestInsight(insights[insights.length - 1] ?? null);
    setNarrative("");
    setNarrativeTitle("");
    setNarrativeMeta("");
    setSeekRequest(null);
  }

  function selectChapter(chapter: RecapChapter) {
    const selected = insights.find((insight) => insight.type === chapter.type
      && insight.provenance?.window?.start_ms === chapter.window_start_ms
      && insight.provenance?.window?.end_ms === chapter.window_end_ms);
    if (selected) selectInsight(selected);
    else setNavigationNotice("The supporting moment for this chapter is not available in the current match.");
  }

  function handleSectionKey(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = ["match", "moments", "story"] as const;
    const index = tabs.indexOf(section);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    setSection(tabs[next]);
    document.getElementById(`workspace-tab-${tabs[next]}`)?.focus();
  }

  async function requestRecap() {
    if (recapBusy) return;
    setRecapBusy(true);
    try { await generateRecap(); }
    finally { setRecapBusy(false); }
  }

  const preferences = <WorkspacePreferences audience={audienceMode} onAudience={(mode) => {
    setAudienceMode(mode);
    if (mode === "player" && playerOptions.length && !playerOptions.some((player) => player.player_id === selectedPlayer)) {
      setSelectedPlayer(playerOptions[0].player_id);
    }
  }} language={language} onLanguage={setLanguage} player={selectedPlayer} onPlayer={setSelectedPlayer}
    players={playerOptions} teams={teamNames} />;
  const commentaryControls = <CommentaryControls live={liveCommentaryEnabled} dual={dualCommentaryEnabled}
    onLive={setLiveCommentaryEnabled} onDual={setDualCommentaryEnabled}
    status={pinnedInsightRef.current && liveCommentaryEnabled && speechStatus === "stopped" ? "Live commentary paused for the selected moment." : speechStatus}
    onStop={() => stopSpeech()} showStop={Boolean(matchId)} />;

  return (
    <div className="app-shell studio-shell">
      <a href="#workspace-content" className="skip-link">Skip to workspace</a>
      <header className="studio-nav">
        <Brand /><span className="studio-nav-label">The match studio</span>
        <nav className="studio-nav-actions" aria-label="Workspace navigation">
          <Link href="/" className="btn btn--quiet btn--sm"><ArrowLeft size={14} aria-hidden="true" />Home</Link>
          {canAnalyze && matchId ? <button type="button" className="btn btn--outline btn--sm" disabled={Boolean(jobId) || submittingVideo || isConnecting}
            onClick={() => { setShowEntry(true); requestAnimationFrame(() => document.getElementById("match-setup")?.scrollIntoView({ block: "start" })); }}>
            <Plus size={15} aria-hidden="true" />New match
          </button> : null}
        </nav>
      </header>
      <main id="workspace-content" className={matchId ? "workspace-open" : undefined} tabIndex={-1}>
        <div className="workspace-access-region"><SessionAccess onSessionChanged={resetWorkspaceSession} onAccessChanged={setAccess} onCapabilitiesChanged={setCapabilities}
          refreshRevision={sessionRevision} signInRequired={signInRequired} /></div>
        {canRead ? (
          <>
          <div className="studio-heading">
            <div><p className="eyebrow"><span className="signal-dot" />Your perspective, in play</p>
              <h1>{matchId ? "The match, in focus." : "A better view of the game."}</h1></div>
            <p>{matchId ? "One match. Connected moments, replay, and the evidence behind the story." : "Start a scenario, bring your footage, or pick up where you left off."}</p>
          </div>

          {matchId ? <>{preferences}{commentaryControls}</> : null}
          {capabilities && matchId ? (
            <p className="service-awareness">{capabilities.speech_configured && CLOUD_SPEECH_ENABLED
              ? "Azure Speech configured. Playback commentary and Listen can make metered cloud calls."
              : "Cloud speech is disabled or unconfigured. Browser narration requires matching-language voices; dual mode needs two distinct male voices."}
              {capabilities.narrative?.status === "disabled" ? " Explanations use grounded local templates." : null}</p>
          ) : null}
          {isCloud && !canStartAnalysis && canAnalyze ? <p className="notice" role="status">
            {analysisPausedUntil > Date.now() ? `New analyses are temporarily paused. Retry after ${sessionTime(analysisPausedUntil / 1000)}. `
              : capabilities ? "New analyses are unavailable until this server enables an approved processing allocation. "
                : "Checking availability for new analyses. "}
            Existing owned matches, replay, Listen and saved history remain available. Signing in does not bypass processing limits.
          </p> : null}
          {audioNotice ? <p className="notice" role="status">{audioNotice}</p> : null}

          {navigationNotice ? <p className="notice" role="status">{navigationNotice}</p> : null}
          {!jobId && !submittingVideo && videoStatus !== "idle" && (entryTab !== "video" || !showEntry)
            ? <p className="notice notice--quiet" role="status">{videoStatus}</p> : null}
          {!["idle", "live", "completed", "paused", "ready", "failed"].includes(status) && !isConnecting
            ? <p className="notice" role="status">{status}</p> : null}
          {jobId || submittingVideo ? <section className="job-notice" aria-label="Analysis progress">
            <div><Loader2 size={21} className="icon-spin" aria-hidden="true" /><div><strong>{submittingVideo ? "Preparing your video." : "Your analysis is in progress."}</strong>
              <p role="status">{videoStatus === "idle" ? "Submitting your analysis..." : videoStatus}</p>
              {submittingVideo ? <p>Keep this page open until the video is queued. Queued analysis can continue in the background.</p> : null}</div></div>
            {jobId && canAnalyze ? <button type="button" className="btn btn--outline btn--sm" onClick={() => void cancelAnalysis()}>Cancel analysis</button> : null}
          </section> : null}

          {!matchId || showEntry ? <div className={!matchId ? "entry-layout" : ""} id="match-setup">
            {!matchId ? <div className="entry-intro">
              <p className="eyebrow">Your game. Your point of view.</p>
              <h2>{canAnalyze ? <>Start with a match.<br />Find the <em>perspective.</em></> : <>Your shared matches.<br />A <em>clearer perspective.</em></>}</h2>
              <p>{canAnalyze ? "See a pattern. Revisit the moment. Understand why it mattered. Everything starts with the match you choose."
                : "Your viewer access lets you explore saved matches and their explanations. Ask your workspace administrator for analysis permissions."}</p>
              <div className="entry-illustration"><PitchIllustration id="workspace-entry" /></div>
              <p className="entry-caption">Illustrative tactics diagram · not tracked match observations</p>
            </div> : null}
            <EntryPanel tab={entryTab} onTabChange={setEntryTab} canAnalyze={canAnalyze} busy={isConnecting || Boolean(jobId) || submittingVideo}
              admissionDisabled={!canStartAnalysis}
              hasMatch={Boolean(matchId)} onClose={() => setShowEntry(false)}
              teamAName={teamAName} teamBName={teamBName} onTeamAName={setTeamAName} onTeamBName={setTeamBName}
              scenario={selectedScenario} onScenario={setSelectedScenario} onStart={() => void startDemo()}
              file={selectedVideoFile} onFile={setSelectedVideoFile} url={videoUrl} onUrl={setVideoUrl}
              rightsConfirmed={rightsConfirmed} onRightsConfirmed={setRightsConfirmed}
              youtubeConfigured={capabilities?.youtube_import_configured}
              analysisMode={analysisMode} onAnalysisMode={setAnalysisMode} cvOptions={cvOptionsText} onCvOptions={setCvOptionsText}
              publicTrial={isCloud}
              onUpload={() => void uploadVideo()} onLink={() => void ingestVideoFromLink()} videoStatus={videoStatus}
              saved={savedMatches} onRestore={(id) => void loadSavedMatch(id)}>
              {entryTab === "saved" && savedNextAfter ? <button type="button" className="btn btn--outline btn--sm" onClick={() => void loadMoreSaved()}>Load more saved matches</button> : null}
              {!matchId ? <>{commentaryControls}<details className="entry-preferences"><summary>Perspective &amp; language preferences</summary>{preferences}</details></> : null}
            </EntryPanel>
          </div> : null}

          {matchId ? (
            <>
              <section id="match-stage" data-match-id={matchId} aria-label="Match status">
                <div className="match-bar">
                  <div className="match-teams">
                    <div className="team-block"><span className="team-badge" aria-hidden="true">{teamMonogram(teamNames.team_a)}</span><span>{teamNames.team_a}</span></div>
                    <div className="score-block"><span>{typeof displayedScore?.team_a === "number" ? displayedScore.team_a : "–"}</span><span className="score-sep">:</span><span>{typeof displayedScore?.team_b === "number" ? displayedScore.team_b : "–"}</span></div>
                    <div className="team-block"><span className="team-badge team-badge--b" aria-hidden="true">{teamMonogram(teamNames.team_b)}</span><span>{teamNames.team_b}</span></div>
                  </div>
                  <div className="match-bar-meta"><span className="match-clock">{formatMs(displayedClock)}</span>
                    <span className={`live-pill live-pill--${statusMeta.tone}`}><StatusIcon size={12} aria-hidden="true" />{statusMeta.label}</span>
                    <span className="tag">{sourceLabel}</span>
                    {canAnalyze && !isCloud && (status === "live" || status === "paused") ? <button className="btn btn--quiet btn--sm" type="button" onClick={() => void toggleMatchPause()}>{status === "paused" ? "Resume match" : "Pause match"}</button> : null}
                  </div>
                </div>
                <div className="match-stage-composition">
                  <div className="match-stage">
                    <TacticalReplay ref={replayRef} key={matchId} matchId={matchId} status={status} selectedPlayer={selectedPlayer}
                      paginated={isCloud}
                      liveTracking={liveState?.cv_live} liveClockMs={liveState?.match_clock_ms} seekRequest={seekRequest}
                      onPlayback={onReplayPlayback} onInteraction={onReplayInteraction}
                      onSelectInsight={selectInsightById} onReturnToCurrent={followLatestInsight} showWarnings={false}
                      onSelectPlayer={(id) => { setSelectedPlayer(id); setAudienceMode("player"); }} />
                  </div>
                  <SelectedMoment insight={heroInsight} narrative={narrative} title={narrativeTitle} meta={narrativeMeta}
                    overviewAvailable={overviewAvailable}
                    busy={speechModeRef.current === "manual" && speakingRef.current}
                    narrativeInsightId={narrativeInsightRef.current} language={language} following={isConnecting || status === "live"}
                    onListen={speakCurrentText} onExplore={() => {
                      setSection("moments");
                      requestAnimationFrame(() => document.getElementById("workspace-tab-moments")?.focus());
                    }} />
                </div>
                {transcript ? <details className="commentary-transcript" aria-label="Commentary transcript">
                  <summary>{transcript.depth === "deep" ? "In-depth breakdown" : "Playback commentary"} · transcript</summary>
                  <p className="status-line">{transcript.provider} · {transcript.fallback_used ? "fallback explanation, not a live model response" : "model-generated explanation"}
                    {transcript.provider_failure?.reason ? ` · model ${transcript.provider_failure.reason}` : ""}</p>
                  <div lang={language} dir="auto">{transcript.turns.map((turn, index) => <p key={index}>
                    <strong>{turn.role === "lead" ? "Lead" : "Analyst"}</strong><span>{turn.text}</span>
                  </p>)}</div>
                </details> : null}
              </section>
              {liveState?.analysis_metadata?.insights_truncated === true ? <p className="notice" role="status">
                Showing up to 100 generated moments in chronological order. The full replay and any available source footage remain accessible.
              </p> : null}
              {liveState?.analysis_metadata?.warnings?.length ? <ul className="analysis-notices" aria-label="Analysis limitations">{liveState.analysis_metadata.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}

              <div className="workspace-tabs" role="tablist" aria-label="Match workspace sections" onKeyDown={handleSectionKey}>
                {(["match", "moments", "story"] as const).map((tab) => <button type="button" role="tab" key={tab} id={`workspace-tab-${tab}`}
                  aria-selected={section === tab} aria-controls={`workspace-panel-${tab}`} tabIndex={section === tab ? 0 : -1}
                  onClick={() => setSection(tab)}>{tab === "match" ? "The match" : tab === "moments" ? `Moments${insights.length ? ` (${insights.length})` : ""}` : "The story"}</button>)}
              </div>

              <section className="workspace-panel" role="tabpanel" id="workspace-panel-match" aria-labelledby="workspace-tab-match" hidden={section !== "match"}>
                <div className="match-overview-layout">
                  <aside className="signals-panel" aria-label="Match summary">
                    <div className="signals-heading">A little context. The bigger picture.</div>
                    <div className="signals-row">
                      <div className="signal-metric"><span>Supported moments</span><strong>{insights.length}</strong></div>
                      <div className="signal-metric"><span>Turning points</span><strong>{turningPoints}</strong></div>
                      <div className="signal-metric"><span>Football actions</span><strong>{eventsCount}</strong></div>
                    </div>
                    <div className="signal-footer">{sourceLabel} · {isVideoAnalysis ? "Analyzed segment, not necessarily a full match" : "A simulated match excerpt"}
                      {typeof liveState?.tracking_sample_count === "number" ? ` · ${liveState.tracking_sample_count} tracking samples, counted separately` : ""}</div>
                    {trackingQuality ? <p className="status-line tracking-quality">
                      Ball candidates observed in {trackingQuality.ball_observed_frames ?? 0} / {trackingQuality.sampled_frames ?? 0} samples.
                      {" "}{trackingQuality.ball_predicted_frames ?? 0} short predictions · {trackingQuality.scene_changes ?? 0} detected scene changes.
                      This is observation coverage, not measured tracking accuracy.
                    </p> : null}
                    {audienceMode !== "fan" ? <details className="summary-detail"><summary>More match signals</summary><div className="evidence-grid">
                      <div className="evidence-cell"><span className="metric">Observed players</span><span className="value">{trackedPlayers || "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Ball speed</span><span className="value">{typeof ballSpeed === "number" ? `${ballSpeed.toFixed(1)} km/h` : "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Top player speed</span><span className="value">{typeof topPlayerSpeed === "number" ? `${topPlayerSpeed.toFixed(1)} km/h` : "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Possession</span><span className="value">{teamLabel(possessionTeamId)}</span></div>
                      <div className="evidence-cell"><span className="metric">Team control</span><span className="value">{typeof controlA === "number" && typeof controlB === "number" ? `${teamNames.team_a} ${controlA} · ${teamNames.team_b} ${controlB}` : "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Control difference</span><span className="value">{typeof controlDelta === "number" ? controlDelta : "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Control leader</span><span className="value">{controlLeader === "neutral" ? "Neutral" : teamLabel(controlLeader)}</span></div>
                      <div className="evidence-cell"><span className="metric">Pitch zone</span><span className="value">{pitchZone ? readableLabel(pitchZone) : "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Ball direction</span><span className="value">{ballDirection ? readableLabel(ballDirection) : "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Possession transition confidence</span><span className="value">{typeof possessionTransitionConfidence === "number" ? `${Math.round(possessionTransitionConfidence * 100)}%` : "—"}</span></div>
                      <div className="evidence-cell"><span className="metric">Team identification confidence</span><span className="value">{typeof teamIdConfidence === "number" ? `${Math.round(teamIdConfidence * 100)}%` : "—"}</span></div>
                    </div></details> : null}
                  </aside>
                </div>
              </section>

              <section className="workspace-panel" role="tabpanel" id="workspace-panel-moments" aria-labelledby="workspace-tab-moments" hidden={section !== "moments"}>
                <div className="section-head"><span className="section-title">Follow the moments that matter.</span><span className="section-hint">Select a moment to revisit its replay and explanation.</span></div>
                <div className="explorer-bar">
                  <div className="explorer-filter"><label htmlFor="timeline-filter" className="sr-only">Filter by insight type</label><select id="timeline-filter" value={timelineFilter} onChange={(event) => setTimelineFilter(event.target.value)}>
                    <option value="all">All moments</option>{timelineTypes.map((type) => <option key={type} value={type}>{prettyType(type)} ({typeCounts[type]})</option>)}
                  </select></div>
                  <div className="explorer-search"><Search size={14} aria-hidden="true" /><label htmlFor="timeline-search" className="sr-only">Search insights</label><input id="timeline-search" type="search" placeholder="Search a pattern or player..." value={timelineSearch} onChange={(event) => setTimelineSearch(event.target.value)} /></div>
                  <span className="explorer-count">{filteredInsights.length} moments</span>
                </div>
                {filteredInsights.length ? <div className="moment-list">{[...filteredInsights].reverse().map((insight, index) => <MomentCard key={insight.insight_id ?? `${insight.title}-${index}`} insight={insight} selected={insight.insight_id === heroInsight?.insight_id} active={section === "moments"} onSelect={() => selectInsight(insight)} />)}</div>
                  : <div className="empty-state">{insights.length ? "No moments match these filters." : "Supported moments will appear here when available."}</div>}
              </section>

              <section className="workspace-panel" role="tabpanel" id="workspace-panel-story" aria-labelledby="workspace-tab-story" hidden={section !== "story"}>
                <div className="section-head"><div><p className="eyebrow">The full picture</p><h2>{isVideoAnalysis ? "Your analysis story." : "The story of this match."}</h2></div>
                  <div className="btn-row"><button type="button" className="btn btn--outline btn--sm" disabled={!insights.length && !overviewAvailable} onClick={() => void generateNarrative()}>{insights.length ? "Generate narrative" : "Generate overview"}</button>
                    {isCloud && capabilities?.paid_generation_enabled === false && capabilities.deterministic_recap_enabled !== true ? <span className="status-line">Full-match recaps are unavailable on this server. Explore individual moments and Listen instead.</span>
                      : <button type="button" className="btn btn--primary btn--sm" disabled={!insights.length || recapBusy} onClick={() => void requestRecap()}>{recapBusy ? <Loader2 size={14} className="icon-spin" aria-hidden="true" /> : <Sparkles size={14} aria-hidden="true" />}{recapBusy ? "Preparing recap..." : "Generate recap"}</button>}</div>
                </div>
                <div className="story">
                  {recapMeta && !recap ? <p className="notice" role="status">{recapMeta}</p> : null}
                  {recapMeta.includes("fallback=true") ? <p className="explanation-source">Fallback recap · no live model response</p> : null}
                  {recap ? <div className="story-lede"><p lang={language} dir="auto">{recap}</p>
                    <button type="button" className="btn btn--outline btn--sm" disabled={speechModeRef.current === "manual" && speakingRef.current}
                      onClick={() => void requestCommentary("manual", undefined, undefined, {
                        turns: [{ role: "lead", text: recap }], provider: "written-recap",
                        fallback_used: recapMeta.includes("fallback=true"), depth: "deep",
                      })}>Read recap aloud · lead voice</button>
                    {recapMeta ? <details className="source-details"><summary>Recap source details</summary><p className="status-line">{recapMeta}</p></details> : null}</div>
                    : narrative ? <div className="story-lede"><p lang={language} dir="auto">{narrative}</p></div>
                      : <div className="empty-state">Make the match yours. Generate a supported explanation or a recap in your chosen perspective and language.</div>}
                  {recapChapters.length ? <div className="story-chapters">{recapChapters.map((chapter) => <article className="story-chapter" key={chapter.chapter_id}>
                    <span className="eyebrow">{formatMs(chapter.window_start_ms)} — {formatMs(chapter.window_end_ms)}</span><h4 lang={language} dir="auto">{chapter.title}</h4><p lang={language} dir="auto">{chapter.summary}</p>
                    <span className="chapter-meta">{Math.round(chapter.confidence * 100)}% signal confidence</span>
                    <button type="button" className="btn btn--quiet btn--sm" onClick={() => selectChapter(chapter)}>Revisit this moment<ArrowUpRight size={14} aria-hidden="true" /></button>
                  </article>)}</div> : null}
                </div>
              </section>

              <div className="utility-bar">
                <div className="export-row" aria-label="Export results"><span className="context-label">Take it with you</span><button type="button" className="btn btn--outline btn--sm" disabled={!insights.length} onClick={exportInsightsJson}><Download size={13} aria-hidden="true" />Insights JSON</button>
                  <button type="button" className="btn btn--outline btn--sm" disabled={!insights.length} onClick={exportInsightsCsv}><Download size={13} aria-hidden="true" />Insights CSV</button></div>
                <span className="status-line">match {matchId.slice(-8)} · {eventsCount} events</span>
              </div>
              {canInspect ? <details className="advanced-panel">
                <summary>Advanced controls · analysis audit</summary><div className="advanced-grid">
                  <div className="sheet-section"><span className="field-label">Commentary orchestration</span><p className="status-line">Inspect the actual provider path and any configured profile versions. Fallback instructions are not remote profile verification.</p>
                    <button type="button" className="btn btn--outline" onClick={() => void fetchCommentaryAudit()}>Refresh commentary audit</button><p className="status-line" role="status">{commentaryAuditStatus}</p>
                    {commentaryAudit.map((entry) => <div className="audit-entry" key={entry.call_id}><span className="who">{entry.provider} · {entry.fallback_used ? "Fallback" : "Primary path"}</span>
                      <p><strong>Lead:</strong> {entry.primary_text}</p><p><strong>Analyst:</strong> {entry.secondary_text}</p>
                      {(entry.audit?.agents ?? []).map((agent) => <details key={`${entry.call_id}-${agent.name}-${agent.version}`}><summary>{agent.name} · {agent.version ?? "unverified"} · {agent.source}</summary>{agent.instructions ? <p className="agent-instructions">{agent.instructions}</p> : null}</details>)}
                    </div>)}
                  </div>
                  <div className="sheet-section"><span className="field-label">Data and interpretation</span><p className="status-line">{liveState?.analysis_metadata?.notes ?? "Scenario events are simulated. Their source is not a real match video."}</p>
                    {videoSummary && isVideoAnalysis ? <p className="status-line">{videoSummary.event_count} events · {videoSummary.insight_count} moments{typeof videoSummary.clips_generated === "number" ? ` · ${videoSummary.clips_generated} generated clips` : ""}</p> : null}
                    <p className="status-line">Last received action: {latestEvent?.type ? readableLabel(latestEvent.type) : "unavailable"} · sequence {latestEvent?.sequence ?? "unavailable"}</p>
                    <p className="status-line">Uncalibrated image coordinates are not physical pitch geometry. Unvalidated xT/xG and synthetic preview events are not production football evidence.</p>
                    <p className="status-line">Current average signal confidence: {insights.length ? `${Math.round(avgConfidence * 100)}%` : "unavailable"}. Confidence is not measured accuracy.</p>
                  </div>
                </div>
              </details> : null}
              {!isCloud && access.principal?.role === "admin" ? <OperationsPanel revision={sessionRevision} /> : null}
            </>
          ) : null}
          <footer className="workspace-footer"><p>Evidence before explanation. Simulated, inferred, and model-derived sources stay labelled.</p><Link href="/">Tactiq · a different perspective<ArrowUpRight size={13} aria-hidden="true" /></Link></footer>
          </>
        ) : null}
      </main>
    </div>
  );

}
