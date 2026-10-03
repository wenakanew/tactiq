"use client";

import { useMemo, useRef, useState } from "react";

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
};

type VideoIngestionResponse = {
  match_id: string;
  source: string;
  extractor: string;
  extractor_confidence: number;
  notes: string;
  event_count: number;
  insight_count: number;
  insights: Insight[];
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function voicePairFor(language: string): { primary: string; secondary: string } {
  switch (language) {
    case "en-GB":
      return { primary: "en-GB-RyanNeural", secondary: "en-GB-ThomasNeural" };
    case "sw-KE":
      return { primary: "sw-KE-RafikiNeural", secondary: "en-GB-ThomasNeural" };
    case "fr-FR":
      return { primary: "fr-FR-HenriNeural", secondary: "en-GB-ThomasNeural" };
    default:
      return { primary: "en-GB-RyanNeural", secondary: "en-GB-ThomasNeural" };
  }
}

function browserVoicePair(language: string): { primary: SpeechSynthesisVoice | null; secondary: SpeechSynthesisVoice | null } {
  if (typeof window === "undefined" || !window.speechSynthesis) {
    return { primary: null, secondary: null };
  }

  const voices = window.speechSynthesis.getVoices();
  const localeVoices = voices.filter((v) => v.lang?.toLowerCase().startsWith(language.toLowerCase().split("-")[0]));
  const primary = localeVoices[0] ?? voices[0] ?? null;
  const secondary = localeVoices[1] ?? voices[1] ?? primary;
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
  const [speechStatus, setSpeechStatus] = useState<string>("idle");
  const [videoStatus, setVideoStatus] = useState<string>("idle");
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [selectedVideoFile, setSelectedVideoFile] = useState<File | null>(null);
  const [videoSummary, setVideoSummary] = useState<VideoIngestionResponse | null>(null);
  const [liveCommentaryEnabled, setLiveCommentaryEnabled] = useState<boolean>(true);
  const [dualCommentaryEnabled, setDualCommentaryEnabled] = useState<boolean>(true);
  const wsRef = useRef<WebSocket | null>(null);
  const speechQueueRef = useRef<Array<{ primary: string; secondary?: string }>>([]);
  const speakingRef = useRef<boolean>(false);

  const wsUrl = useMemo(() => API_BASE.replace("http", "ws"), []);

  async function fetchDualCommentary(insightId?: string): Promise<{ primary_text: string; secondary_text: string } | null> {
    if (!matchId) {
      return null;
    }

    try {
      const response = await fetch(`${API_BASE}/api/v1/matches/${matchId}/commentary/dual`, {
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
      return payload;
    } catch {
      return null;
    }
  }

  async function startDemo() {
    setStatus("creating match...");
    setInsights([]);
    setLatestInsight(null);
    setEventsCount(0);
    setNarrative("");
    setNarrativeMeta("");
    setRecap("");
    setRecapMeta("");
    setVideoSummary(null);

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
  }

  async function uploadVideo() {
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
    setVideoStatus(`done • ${payload.extractor} • ${payload.event_count} events`);
  }

  async function ingestVideoFromLink() {
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
    setVideoStatus(`done • ${payload.extractor} • ${payload.event_count} events`);
  }

  async function generateNarrative() {
    if (!matchId) {
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
    if (!matchId) {
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
      return;
    }

    const payload = await response.json();
    setRecap(payload.body);
    setRecapMeta(
      `${payload.audience_mode} • ${payload.language} • provider=${payload.provider} • fallback=${payload.fallback_used}`,
    );
  }

  async function playSpeech(primary: string, secondary?: string) {
    if (!primary) {
      setSpeechStatus("nothing to speak");
      return;
    }

    setSpeechStatus("requesting azure speech...");
    const response = await fetch(secondary ? `${API_BASE}/api/v1/speech/synthesize-dual` : `${API_BASE}/api/v1/speech/synthesize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        secondary
          ? {
              primary_text: primary,
              secondary_text: secondary,
              language,
              primary_voice: voicePairFor(language).primary,
              secondary_voice: voicePairFor(language).secondary,
            }
          : {
              text: primary,
              language,
              voice: voicePairFor(language).primary,
              rate: "+3%",
              pitch: "+1st",
            },
      ),
    });

    if (response.ok) {
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

    // Fallback to browser speech if cloud speech is unavailable.
    if (typeof window === "undefined" || !window.speechSynthesis) {
      setSpeechStatus("speech unavailable (cloud and browser unsupported)");
      return;
    }

    window.speechSynthesis.cancel();
    const pair = browserVoicePair(language);

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
      await speakOnce(primary, pair.primary, 1.02, 1.03);
      if (secondary) {
        await speakOnce(secondary, pair.secondary, 0.98, 0.96);
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
      ? "Absolutely. You can feel the tempo shift, and that pressure is forcing mistakes."
      : undefined;
    enqueueSpeech(primary, secondary);
  }

  function connectWs(id: string) {
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
            void fetchDualCommentary(next.insight_id).then((scripted) => {
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

  return (
    <main>
      <h1>Match Intelligence — Deterministic Slice</h1>
      <p>
        Start the seeded scenario and verify an evidence-backed <code>Pressure Building</code> insight.
      </p>

      <div className="card">
        <label htmlFor="scenario">Scenario:</label>{" "}
        <select
          id="scenario"
          value={selectedScenario}
          onChange={(event) => setSelectedScenario(event.target.value)}
        >
          <option value="sustained_pressure">sustained_pressure</option>
          <option value="rhythm_shift">rhythm_shift</option>
          <option value="player_influence">player_influence</option>
        </select>{" "}
        <button onClick={startDemo}>Start scenario</button>
        <p>Status: {status}</p>
        {matchId ? <p>Match: <code>{matchId}</code></p> : null}
        <p>Events streamed: {eventsCount}</p>
        {scenarios.length > 0 ? <p>Scenarios loaded: {scenarios.length}</p> : null}
      </div>

      <div className="card">
        <h2>Video upload</h2>
        <p>Upload a clip to run auto-eventing and push extracted events through the intelligence pipeline.</p>
        <input
          type="file"
          accept="video/*"
          onChange={(event) => setSelectedVideoFile(event.target.files?.[0] ?? null)}
        />{" "}
        <button onClick={uploadVideo}>Upload video</button>
        <div style={{ marginTop: 8 }}>
          <input
            type="url"
            placeholder="https://example.com/video.mp4"
            value={videoUrl}
            onChange={(event) => setVideoUrl(event.target.value)}
            style={{ width: "70%" }}
          />{" "}
          <button onClick={ingestVideoFromLink}>Ingest from link</button>
        </div>
        <p>Video status: {videoStatus}</p>
        {videoSummary ? (
          <p>
            extractor=<code>{videoSummary.extractor}</code> • confidence={Math.round(videoSummary.extractor_confidence * 100)}% •
            events={videoSummary.event_count} • insights={videoSummary.insight_count}
          </p>
        ) : null}
      </div>

      <div className="card">
        <h2>Narrative controls</h2>
        <label htmlFor="mode">Mode:</label>{" "}
        <select id="mode" value={audienceMode} onChange={(event) => setAudienceMode(event.target.value)}>
          <option value="fan">fan</option>
          <option value="analyst">analyst</option>
          <option value="player">player</option>
        </select>{" "}
        <label htmlFor="lang">Language:</label>{" "}
        <select id="lang" value={language} onChange={(event) => setLanguage(event.target.value)}>
          <option value="en-GB">en-GB</option>
          <option value="sw-KE">sw-KE</option>
          <option value="fr-FR">fr-FR</option>
        </select>{" "}
        <label htmlFor="player">Player:</label>{" "}
        <select id="player" value={selectedPlayer} onChange={(event) => setSelectedPlayer(event.target.value)}>
          <option value="a_10">a_10</option>
          <option value="b_08">b_08</option>
        </select>{" "}
        <button onClick={generateNarrative}>Generate narrative</button>
        <button onClick={generateRecap}>Generate recap</button>
        <button onClick={speakCurrentText}>Speak current text</button>
        <label htmlFor="live-commentary" style={{ marginLeft: 12 }}>
          <input
            id="live-commentary"
            type="checkbox"
            checked={liveCommentaryEnabled}
            onChange={(event) => setLiveCommentaryEnabled(event.target.checked)}
          />{" "}
          live commentary
        </label>
        <label htmlFor="dual-commentary" style={{ marginLeft: 12 }}>
          <input
            id="dual-commentary"
            type="checkbox"
            checked={dualCommentaryEnabled}
            onChange={(event) => setDualCommentaryEnabled(event.target.checked)}
          />{" "}
          dual commentary
        </label>
        <p>Speech: {speechStatus}</p>
        {narrativeMeta ? <p>{narrativeMeta}</p> : null}
        {narrative ? <p>{narrative}</p> : <p>No narrative yet.</p>}
        {recapMeta ? <p>{recapMeta}</p> : null}
        {recap ? <p>{recap}</p> : null}
      </div>

      <div className="card">
        <h2>Latest insight</h2>
        {latestInsight ? (
          <>
            <p><strong>{latestInsight.title}</strong> ({Math.round(latestInsight.confidence * 100)}%)</p>
            <p>{latestInsight.summary}</p>
            <ul>
              {latestInsight.evidence.map((item) => (
                <li key={item.metric}>
                  {item.metric}: {item.value}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>No insight yet.</p>
        )}
      </div>

      <div className="card">
        <h2>Insight feed</h2>
        {insights.length === 0 ? <p>No emitted insights.</p> : null}
        {insights.map((insight, index) => (
          <p key={insight.insight_id ?? `${insight.title}-${index}`}>
            <strong>{insight.title}</strong> — {insight.summary}
          </p>
        ))}
      </div>
    </main>
  );
}
