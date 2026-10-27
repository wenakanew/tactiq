"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import Brand from "../Brand";
import TacticalReplay from "../TacticalReplay";
import SelectedMoment from "./SelectedMoment";
import { CONTENT_LANGUAGES, clockLabel, evidenceMetrics, readableLabel } from "../../lib/workspace-types";
import { isPublicExample, type PublicExample, type ExampleAudience, type ExampleLanguage } from "../../lib/public-example";
import type { ReplayControls } from "../../lib/playback-commentary";

export default function ExampleMatch() {
  const [example, setExample] = useState<PublicExample | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [audience, setAudience] = useState<ExampleAudience>("fan");
  const [language, setLanguage] = useState<ExampleLanguage>("en-GB");
  const [player, setPlayer] = useState("");
  const [seek, setSeek] = useState<{ timeMs: number; serial: number } | null>(null);
  const [audioError, setAudioError] = useState("");
  const [sampleAudioError, setSampleAudioError] = useState("");
  const replay = useRef<ReplayControls>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const matchAudio = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/examples/pressure-v1.json", { signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error(`Example unavailable (${response.status}).`);
      const data: unknown = await response.json();
      if (!isPublicExample(data)) throw new Error("The prepared example is incomplete.");
      setExample(data);
      const first = data.insights.find((item) => item.type === "sustained_pressure") ?? data.insights[0];
      setSelected(first.insight_id);
      setSeek({ timeMs: first.provenance?.window?.end_ms ?? 0, serial: 1 });
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Example could not be opened.");
    });
    return () => controller.abort();
  }, []);

  const moment = example?.insights.find((item) => item.insight_id === selected) ?? null;
  const narrative = moment?.narratives[language][audience];
  const recap = example?.recaps[language][audience];
  const matchingAudio = example && moment?.insight_id === example.audio.insight_id
    && language === example.audio.language && audience === example.audio.audience;
  function choose(id: string) {
    const next = example?.insights.find((item) => item.insight_id === id);
    if (!next) return;
    audio.current?.pause();
    matchAudio.current?.pause();
    setAudioError("");
    setSelected(id);
    setSeek((previous) => ({ timeMs: next.provenance?.window?.end_ms ?? 0, serial: (previous?.serial ?? 0) + 1 }));
  }
  function exploreEvidence() {
    const details = document.getElementById("example-provenance");
    if (details instanceof HTMLDetailsElement) {
      details.open = true;
      details.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  return <div className="example-page page-width">
    <a className="skip-link" href="#example-content">Skip to example match</a>
    <header className="site-header"><Brand /><nav aria-label="Example navigation">
      <Link href="/" className="btn btn--quiet">Home</Link>
      <Link href="/workspace" prefetch={false} className="btn btn--outline">Analyze a new match</Link>
    </nav></header>
    <main id="example-content">
      <p className="eyebrow">Explore an example match</p>
      <h1>Why is the pressure building?</h1>
      <p className="notice" role="note">Interactive synthetic example. Analysis, localized explanations and recap were prepared beforehand using deterministic domain templates.
        Exploring this example does not start a new analysis or make new paid AI calls. It does not access private workspaces.</p>
      <p>Play the passage, inspect the evidence, then compare Fan and Analyst. The separate Azure audio sample below is not commentary for this selected match.</p>
      {error ? <div role="alert" className="error-notice">{error} <button type="button" className="btn btn--outline" onClick={() => window.location.reload()}>Retry example</button></div>
        : !example ? <p role="status">Opening the prepared example...</p> : null}
      {example && moment && narrative ? <>
        <div className="example-preferences">
          <div className="segmented" role="group" aria-label="Example perspective">
            {(["fan", "analyst"] as const).map((value) => <button key={value} type="button" aria-pressed={audience === value}
              onClick={() => { matchAudio.current?.pause(); setAudioError(""); setAudience(value); }}>{value === "fan" ? "Fan" : "Analyst"}</button>)}
          </div>
          <label>Language <select aria-label="Example language" value={language} onChange={(event) => {
            const next = CONTENT_LANGUAGES.find(([locale]) => locale === event.target.value);
            if (next) { audio.current?.pause(); matchAudio.current?.pause(); setAudioError(""); setLanguage(next[0]); }
          }}>{CONTENT_LANGUAGES.map(([locale, label]) => <option key={locale} value={locale}>{label}</option>)}</select></label>
        </div>
        <section className="example-match-grid" aria-label="Prepared match and explanation">
          <TacticalReplay ref={replay} matchId={example.id} status="completed" selectedPlayer={player}
            onSelectPlayer={setPlayer} preparedPayload={example.replay} seekRequest={seek} onSelectInsight={choose} />
          <SelectedMoment insight={moment} narrative={narrative.body} title={narrative.title} meta="provider=deterministic; fallback=true; prepared public synthetic example"
            narrativeInsightId={moment.insight_id} language={language} following={false} onListen={exploreEvidence}
            listenLabel="How was this made?" onExplore={() => {
              const chapters = document.getElementById("example-moments");
              chapters?.scrollIntoView({ block: "nearest", behavior: "smooth" });
              chapters?.focus({ preventScroll: true });
            }} />
        </section>
        <section aria-label="Selected explanation audio" className="example-recap">
          <h2>Listen to this explanation</h2>
          {matchingAudio ? <>
            <p>{example.audio.note} Playback is independent of the replay clock.</p>
            <audio ref={matchAudio} controls preload="none" src={example.audio.url} aria-label="Prepared selected explanation"
              onPlay={() => { replay.current?.pause(); audio.current?.pause(); setAudioError(""); }}
              onError={() => setAudioError("Prepared audio is unavailable. Read the selected explanation above.")} />
            {audioError ? <p role="alert" className="error-notice">{audioError}</p> : null}
          </> : <p>No prepared audio for this moment, language and perspective. Select Pressure Building, English and Fan for the matching offline recording. The written explanation remains available.</p>}
        </section>
        <details id="example-provenance" className="advanced-panel">
          <summary>How was this explanation made?</summary>
          <div className="sheet-section">
            <p><strong>Artifact:</strong> {example.id}, recipe {example.recipe_version}, seed {example.seed}. {example.provenance.source}.</p>
            <p><strong>Selected passage:</strong> {clockLabel(moment.provenance?.window?.start_ms)} to {clockLabel(moment.provenance?.window?.end_ms)}.</p>
            <p><strong>Output:</strong> {example.provenance.narratives}. Generated beforehand, not during this visit. {example.provenance.agent_workflow}.</p>
            <p><strong>Supporting measurements:</strong> {evidenceMetrics(moment).map((item) => `${readableLabel(item.metric)}: ${item.value}${item.unit ? ` ${item.unit}` : ""}`).join("; ")}.</p>
            <p><strong>Events in this window:</strong> {moment.events.map((event) => `${event.event_id}: ${clockLabel(event.match_clock_ms)} ${readableLabel(event.type)}`).join("; ")}.</p>
            <details><summary>Resolve measurements to synthetic events</summary>
              {moment.support.map((item) => <p key={item.metric}><strong>{readableLabel(item.metric)}:</strong> {item.event_ids.join(", ")}</p>)}
            </details>
            <p>{example.provenance.limitations} Non-English templates require independent fluent review. No hallucination-free or single-agent-superiority claim is made.</p>
          </div>
        </details>
        <section id="example-moments" tabIndex={-1} aria-label="Prepared recap" className="example-recap">
          <h2>{recap?.title}</h2><p lang={language} dir="auto">{recap?.body}</p>
          <div className="example-chapters">{example.insights.map((item) => <button type="button" className="btn btn--outline" key={item.insight_id}
            onClick={() => choose(item.insight_id)}>{item.narratives[language][audience].title}</button>)}</div>
          <p className="status-line">Prepared deterministic recap. Selecting a chapter changes the replay and explanation above.</p>
        </section>
        <section aria-label="Separate Azure commentary demonstration" className="example-recap">
          <h2>Hear a separately verified Azure exchange</h2>
          <p>This English recording describes a <strong>different synthetic passage</strong> with four attackers and two defenders. It does not describe the selected example.
            Microsoft Agent Framework coordinated a validated lead-to-analyst handoff, and Azure Speech rendered the saved turns. No provider is contacted here.</p>
          <blockquote><strong>Lead:</strong> In this simulated sequence, Team B create a central overload, with four observed attackers near the ball against two defenders.</blockquote>
          <blockquote><strong>Analyst:</strong> This matters because the local advantage can open central options; however, simulated broadcast-view tracking captures only a brief observed window, not the outcome.</blockquote>
          <audio ref={audio} controls preload="none" aria-label="Previously generated Azure commentary" src="/examples/azure-commentary.mp3"
            onPlay={() => { replay.current?.pause(); matchAudio.current?.pause(); setSampleAudioError(""); }} onError={() => setSampleAudioError("Prepared audio is unavailable. The saved transcript remains available above.")} />
          {sampleAudioError ? <p role="alert" className="error-notice">{sampleAudioError}</p> : null}
          <details className="source-details"><summary>Provenance of this audio only</summary><p>Previously generated Foundry / Agent Framework output with no fallback on that probe.
            Lead describes the observation; analyst adds context and limitations after validated handoff. This was a management-plane probe of existing synthetic evidence, not proof of browser-authenticated generation.
            No such model trace is claimed for the deterministic match explanations above.</p></details>
        </section>
      </> : null}
    </main>
    <footer className="site-footer"><p>Public synthetic example. Private analysis, quotas and paid-provider controls are unchanged.</p><Link href="/workspace">Open your workspace</Link></footer>
  </div>;
}
