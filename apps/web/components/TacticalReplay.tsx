"use client";

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { apiFetch, mediaUrl, responseError } from "../lib/api";
import type { ReplayPayload, TrackingSnapshot } from "../lib/match-types";
import { hasPitchGeometry, mergeReplay, replayAt } from "../lib/replay";
import { containedMediaRect, type MediaRect } from "../lib/media-geometry";
import TrackedPitch from "./TrackedPitch";
import WorldPitch from "./WorldPitch";
import type { PlaybackInteraction, PlaybackSnapshot, ReplayControls } from "../lib/playback-commentary";
import { supportedMoments } from "../lib/workspace-types";
import { loadCloudReplay } from "../lib/cloud-replay";

type Props = {
  matchId: string;
  status: string;
  selectedPlayer: string;
  onSelectPlayer: (id: string) => void;
  liveTracking?: TrackingSnapshot;
  liveClockMs?: number;
  seekRequest?: { timeMs: number; serial: number } | null;
  onSelectInsight?: (id: string) => void;
  showWarnings?: boolean;
  onReturnToCurrent?: () => void;
  onPlayback?: (state: PlaybackSnapshot) => void;
  onInteraction?: (reason: PlaybackInteraction) => void;
  paginated?: boolean;
};

function clock(value: number): string {
  const seconds = Math.floor(value / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

const EMPTY_TRACKING: TrackingSnapshot = { players: [], ball: null };

export default forwardRef<ReplayControls, Props>(function TacticalReplay({ matchId, status, selectedPlayer, onSelectPlayer, liveTracking, liveClockMs, seekRequest, onSelectInsight, showWarnings = true, onReturnToCurrent, onPlayback, onInteraction, paginated = false }, ref) {
  const [payload, setPayload] = useState<ReplayPayload | null>(null);
  const [error, setError] = useState("");
  const [timeMs, setTimeMs] = useState(0);
  const [view, setView] = useState<"2d" | "3d">("2d");
  const [presentation, setPresentation] = useState<"split" | "video" | "pitch">("split");
  const [playing, setPlaying] = useState(false);
  const [seeking, setSeeking] = useState(false);
  const [rate, setRate] = useState(1);
  const [epoch, setEpoch] = useState(0);
  const intent = useRef(0);
  const rateRef = useRef(1);
  const sentPlayback = useRef("");
  const playbackCallback = useRef(onPlayback);
  playbackCallback.current = onPlayback;
  const interactionCallback = useRef(onInteraction);
  interactionCallback.current = onInteraction;
  const duckVolume = useRef<{ original: number; applied: number } | null>(null);
  const [followingCurrent, setFollowingCurrent] = useState(true);
  const [videoSize, setVideoSize] = useState<{ width: number; height: number } | null>(null);
  const [videoContent, setVideoContent] = useState<MediaRect | null>(null);
  const videoStage = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const clockRef = useRef(0);
  const animation = useRef<number | null>(null);
  const videoCallback = useRef<number | null>(null);
  const pendingSeek = useRef<number | null>(null);
  const playbackAnchor = useRef({ mediaMs: 0, wallMs: 0 });
  const activeMatch = useRef("");
  const latestPayload = useRef(payload);
  const cloudAfter = useRef<string | number | null>(null);
  const sourceUrl = payload?.source_url ? mediaUrl(payload.source_url) : null;
  latestPayload.current = payload;

  useEffect(() => {
    const controller = new AbortController();
    let fetching = false;
    if (activeMatch.current !== matchId) {
      activeMatch.current = matchId;
      cloudAfter.current = null;
      setPayload(null);
      setError("");
      setTimeMs(0);
      setPlaying(false);
      setFollowingCurrent(true);
      pendingSeek.current = null;
    }
    async function load() {
      if (fetching) return;
      fetching = true;
      try {
        const previous = latestPayload.current?.match_id === matchId ? latestPayload.current : null;
        const cursor = previous?.frames[previous.frames.length - 1]?.sequence ?? 0;
        let next: ReplayPayload;
        let nextAfter: string | number | null = cloudAfter.current;
        if (paginated) {
          const loaded = await loadCloudReplay(matchId, cloudAfter.current,
            (path) => apiFetch(path, { signal: controller.signal }));
          next = loaded.payload;
          nextAfter = loaded.after;
        } else {
          const response = await apiFetch(`/api/v1/matches/${matchId}/replay${cursor ? `?after_sequence=${cursor}` : ""}`, { signal: controller.signal });
          if (!response.ok) throw new Error(await responseError(response));
          next = await response.json() as ReplayPayload;
        }
        if (!Array.isArray(next.frames) || !Number.isFinite(next.processed_until_ms)) {
          throw new Error("The replay response is incomplete.");
        }
        if (controller.signal.aborted) return;
        cloudAfter.current = nextAfter;
        setPayload(mergeReplay(previous, next));
        setError("");
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Unable to load replay.");
      } finally {
        fetching = false;
      }
    }
    void load();
    const interval = status === "live" || status === "running" ? setInterval(() => void load(), 2000) : undefined;
    return () => {
      controller.abort();
      if (interval) clearInterval(interval);
    };
  }, [matchId, status, paginated]);

  useEffect(() => {
    if (seekRequest) seek(seekRequest.timeMs);
  }, [seekRequest?.serial, sourceUrl]);

  useEffect(() => {
    const element = video.current;
    return () => {
      element?.pause();
      if (animation.current !== null) cancelAnimationFrame(animation.current);
      if (element && videoCallback.current !== null && typeof element.cancelVideoFrameCallback === "function") {
        element.cancelVideoFrameCallback(videoCallback.current);
      }
    };
  }, [sourceUrl]);

  useEffect(() => {
    const element = video.current, stage = videoStage.current;
    if (!element || !stage || !sourceUrl) return;
    const refresh = () => {
      if (!element.videoWidth || !element.videoHeight) return;
      setVideoSize((previous) => previous?.width === element.videoWidth && previous.height === element.videoHeight
        ? previous : { width: element.videoWidth, height: element.videoHeight });
      const rect = containedMediaRect(element.clientWidth, element.clientHeight, element.videoWidth, element.videoHeight);
      setVideoContent((previous) => previous?.left === rect?.left && previous?.top === rect?.top
        && previous?.width === rect?.width && previous?.height === rect?.height ? previous : rect);
    };
    const observer = new ResizeObserver(refresh);
    observer.observe(stage);
    element.addEventListener("loadedmetadata", refresh);
    refresh();
    return () => { observer.disconnect(); element.removeEventListener("loadedmetadata", refresh); };
  }, [sourceUrl]);

  const isLive = status === "live" || status === "running";
  const duration = Math.max(1, sourceUrl ? payload?.duration_ms ?? 0 : payload?.processed_until_ms ?? 0,
    !sourceUrl && isLive ? liveClockMs ?? 0 : 0);
  const displayedTime = followingCurrent && !sourceUrl ? duration : timeMs;
  clockRef.current = displayedTime;
  const sampled = useMemo(() => replayAt(payload?.frames ?? [], displayedTime), [payload, displayedTime]);
  const tracking = followingCurrent && !sourceUrl && isLive && liveTracking ? liveTracking : sampled.frame?.tracking ?? EMPTY_TRACKING;
  const tactics = sampled.frame?.tactics;
  const calibrated = hasPitchGeometry(tracking);
  const simulated = tracking.geometry_source === "simulation";
  const teams = payload?.team_display_names ?? {};

  useImperativeHandle(ref, () => ({
    pause: pausePlayback,
    duck: (enabled) => {
      const element = video.current;
      if (!element) return;
      if (enabled && !duckVolume.current) {
        duckVolume.current = { original: element.volume, applied: Math.min(.12, element.volume) };
        element.volume = duckVolume.current.applied;
      } else if (!enabled && duckVolume.current) {
        if (Math.abs(element.volume - duckVolume.current.applied) < .001) element.volume = duckVolume.current.original;
        duckVolume.current = null;
      }
    },
  }));

  useEffect(() => {
    const signature = `${matchId}:${Math.floor(displayedTime / 250)}:${playing}:${seeking}:${rate}:${epoch}:${followingCurrent}:${sampled.frame?.sequence}`;
    if (signature === sentPlayback.current) return;
    sentPlayback.current = signature;
    playbackCallback.current?.({ matchId, timeMs: displayedTime, playing, seeking, rate, epoch,
      intent: intent.current, followingCurrent, frame: sampled.frame });
  }, [matchId, displayedTime, playing, seeking, rate, epoch, followingCurrent, sampled.frame]);

  function pausePlayback() {
    if (video.current && sourceUrl) video.current.pause();
    if (animation.current !== null) cancelAnimationFrame(animation.current);
    setPlaying(false);
    interactionCallback.current?.("pause");
  }

  function changeRate(value: number) {
    playbackAnchor.current = { mediaMs: clockRef.current, wallMs: performance.now() };
    rateRef.current = value;
    setRate(value);
    setEpoch((value) => value + 1);
    if (video.current && sourceUrl) video.current.playbackRate = value;
    interactionCallback.current?.("rate");
  }

  function seek(value: number) {
    value = Math.max(0, value);
    setFollowingCurrent(false);
    setTimeMs(value);
    setEpoch((value) => value + 1);
    setSeeking(Boolean(sourceUrl));
    interactionCallback.current?.("seek");
    playbackAnchor.current = { mediaMs: value, wallMs: performance.now() };
    if (video.current && sourceUrl) {
      pendingSeek.current = value;
      if (video.current.readyState >= 1) {
        if (Math.abs(video.current.currentTime * 1000 - value) <= 1) {
          pendingSeek.current = null;
          setSeeking(false);
        } else video.current.currentTime = value / 1000;
      }
    }
  }

  function acceptMediaTime(value: number) {
    if (pendingSeek.current !== null) {
      if (Math.abs(value - pendingSeek.current) > 150) return;
      pendingSeek.current = null;
    }
    setTimeMs(value);
  }

  function followVideo() {
    const element = video.current;
    if (!element || element.paused) return;
    if (typeof element.requestVideoFrameCallback === "function") {
      videoCallback.current = element.requestVideoFrameCallback((_now, metadata) => {
        acceptMediaTime(metadata.mediaTime * 1000);
        followVideo();
      });
    } else {
      animation.current = requestAnimationFrame(() => {
        acceptMediaTime(element.currentTime * 1000);
        followVideo();
      });
    }
  }

  function togglePlayback() {
    const element = video.current;
    if (element && sourceUrl) {
      if (element.paused) {
        void element.play().catch(() => setError("Playback was blocked. Use the video play control."));
      } else element.pause();
      return;
    }
    if (playing) {
      pausePlayback();
      return;
    }
    const current = latestPayload.current;
    const first = current?.frames[0]?.timestamp_ms ?? 0;
    const start = clockRef.current >= duration || clockRef.current < first ? first : clockRef.current;
    playbackAnchor.current = { mediaMs: start, wallMs: performance.now() };
    setFollowingCurrent(false);
    setPlaying(true);
    intent.current += 1;
    interactionCallback.current?.("play");
    function tick(now: number) {
      if (activeMatch.current !== matchId) return;
      const end = latestPayload.current?.processed_until_ms ?? 0;
      const anchor = playbackAnchor.current;
      const next = Math.min(end, anchor.mediaMs + (now - anchor.wallMs) * rateRef.current);
      setTimeMs(next);
      if (next < end) animation.current = requestAnimationFrame(tick);
      else setPlaying(false);
    }
    animation.current = requestAnimationFrame(tick);
  }

  return (
    <section className="section replay-section" aria-label="Synchronized tactical replay">
      <div className="section-head">
        <span className="section-title">The match view</span>
        <span className="section-hint">{followingCurrent && !sourceUrl ? "Following the current match state" : "Video, observations, and moments on one clock"}</span>
      </div>
      {error ? <p className="error-notice" role="alert">{error}</p> : null}
      {!payload && !error ? <p role="status">Loading replay...</p> : null}
      {payload ? (
        <>
          {sourceUrl ? <div className="stage-presentation">
            <div className="segmented" role="group" aria-label="Match stage layout">
              <button type="button" aria-pressed={presentation === "split"} onClick={() => setPresentation("split")}>Split view</button>
              <button type="button" aria-pressed={presentation === "video"} onClick={() => setPresentation("video")}>Video only</button>
              <button type="button" aria-pressed={presentation === "pitch"} onClick={() => setPresentation("pitch")}>{calibrated ? "Pitch only" : "Tracking only"}</button>
            </div><span className="status-line">One timeline. Your preferred view.</span>
          </div> : null}
          <div className="replay-grid" data-presentation={sourceUrl ? presentation : "pitch"}>
            {sourceUrl ? (
              <div ref={videoStage} className="replay-video-stage" hidden={presentation === "pitch"} style={{ aspectRatio: `${videoSize?.width ?? payload.metadata.width ?? 16}/${videoSize?.height ?? payload.metadata.height ?? 9}` }}>
                <video ref={video} controls preload="metadata" src={sourceUrl} crossOrigin="use-credentials"
                  onPlay={() => { intent.current += 1; setFollowingCurrent(false); setPlaying(true); interactionCallback.current?.("play"); followVideo(); }}
                  onPause={() => {
                    setPlaying(false);
                    interactionCallback.current?.("pause");
                    if (animation.current !== null) cancelAnimationFrame(animation.current);
                    if (video.current && videoCallback.current !== null && "cancelVideoFrameCallback" in video.current) {
                      video.current.cancelVideoFrameCallback(videoCallback.current);
                    }
                  }}
                  onLoadedMetadata={() => {
                    if (video.current && pendingSeek.current !== null) {
                      if (Math.abs(video.current.currentTime * 1000 - pendingSeek.current) <= 1) {
                        acceptMediaTime(pendingSeek.current);
                        setSeeking(false);
                      } else video.current.currentTime = pendingSeek.current / 1000;
                    }
                  }}
                  onSeeking={() => { setSeeking(true); setEpoch((value) => value + 1); interactionCallback.current?.("seek"); }}
                  onSeeked={() => { setSeeking(false); if (video.current) acceptMediaTime(video.current.currentTime * 1000); }}
                  onRateChange={() => {
                    if (video.current && rateRef.current !== video.current.playbackRate) changeRate(video.current.playbackRate);
                  }}
                  onTimeUpdate={() => { if (video.current?.paused) acceptMediaTime(video.current.currentTime * 1000); }}
                  onError={() => setError("Source video cannot be played: check media format, access, or source availability.")} />
                {videoContent && !seeking ? <svg className="image-tracking-layer" viewBox="0 0 100 100" preserveAspectRatio="none"
                  style={{ left: videoContent.left, top: videoContent.top, width: videoContent.width, height: videoContent.height }} aria-hidden="true">
                  {(tracking.players ?? []).filter((player) => player.image_position).map((player) => (
                    <circle key={player.track_id} cx={player.image_position!.x} cy={player.image_position!.y} r="0.55"
                      fill={player.team_id === "team_a" ? "#72a5ff" : player.team_id === "team_b" ? "#ff73a5" : "#fff"}
                      opacity={player.inferred ? 0.4 : 0.95} />
                  ))}
                  {tracking.ball?.image_position ? <circle cx={tracking.ball.image_position.x}
                    cy={tracking.ball.image_position.y} r="0.8" fill="none" stroke="#ffe67a" strokeWidth=".35"
                    opacity={tracking.ball.inferred ? .35 : Math.max(.5, tracking.ball.confidence)} /> : null}
                </svg> : null}
              </div>
            ) : null}
            <div className="replay-world" hidden={Boolean(sourceUrl) && presentation === "video"}>
              <div className="replay-toolbar">
                <div className="segmented" role="group" aria-label="Replay projection">
                  <button type="button" aria-pressed={view === "2d"} onClick={() => setView("2d")}>{calibrated || tracking.detection_source === "synthetic" ? "2D pitch" : "2D image"}</button>
                  <button type="button" aria-pressed={view === "3d"} disabled={!calibrated} onClick={() => setView("3d")}>2.5D scene</button>
                </div>
                <span className="tag">{simulated ? "Simulated ground plane" : calibrated ? "Calibrated ground plane" : tracking.tracking_source === "event_stream_v1" && tracking.detection_source === "synthetic" ? "Simulated events" : "Image-space tracking"}</span>
              </div>
              {view === "3d" && calibrated
                ? <WorldPitch tracking={tracking} frames={payload.frames} timeMs={displayedTime} />
                : <TrackedPitch tracking={{ ...tracking, frame_width: tracking.frame_width ?? videoSize?.width ?? payload.metadata.width,
                    frame_height: tracking.frame_height ?? videoSize?.height ?? payload.metadata.height }} frames={payload.frames} timeMs={displayedTime}
                    selectedPlayer={selectedPlayer} onSelectPlayer={onSelectPlayer} lanes={tactics?.lanes} />}
              <p className="status-line">
                {simulated ? "Generated scenario positions, not observations from real footage."
                  : calibrated ? "Calibrated ground plane. Marker heights are illustrative, not recovered poses."
                  : tracking.tracking_source === "event_stream_v1" && tracking.detection_source === "synthetic"
                    ? "Simulated event positions, not observed player tracking. 2.5D needs calibration."
                    : "Image-space observations. Surveyed landmarks are required for metric pitch geometry."}
              </p>
            </div>
          </div>
          <div className="replay-controls">
            <button type="button" className="btn btn--ghost btn--sm" onClick={togglePlayback}
              disabled={payload.frames.length === 0}>{playing ? "Pause" : "Play replay"}</button>
            <label htmlFor="replay-clock" className="sr-only">Replay position</label>
            <input id="replay-clock" type="range" min="0" max={duration} step="1"
              value={Math.min(displayedTime, duration)} onChange={(event) => seek(Number(event.target.value))}
              onKeyDown={(event) => {
                if (event.key === "Home" || event.key === "End") {
                  event.preventDefault();
                  seek(event.key === "Home" ? 0 : duration);
                }
              }} />
            <output htmlFor="replay-clock" data-testid="replay-clock">{clock(displayedTime)} / {clock(duration)}</output>
            <label className="replay-rate">Speed<select aria-label="Playback speed" value={rate} onChange={(event) => changeRate(Number(event.target.value))}>
              <option value={.5}>0.5x</option><option value={1}>1x</option><option value={1.5}>1.5x</option><option value={2}>2x</option>
            </select></label>
            {!sourceUrl && !followingCurrent ? <button type="button" className="btn btn--quiet btn--sm" onClick={() => {
              if (animation.current !== null) cancelAnimationFrame(animation.current);
              setPlaying(false);
              interactionCallback.current?.("seek");
              setFollowingCurrent(true);
              onReturnToCurrent?.();
            }}>{status === "live" ? "Return to live" : payload.metadata.insights_truncated ? "Return to replay end" : "Latest moment"}</button> : null}
          </div>
          <div className="replay-moments" role="group" aria-label="Seek to an insight">
            {supportedMoments(payload.insights).map((insight) => (
              <button type="button" className="btn btn--ghost btn--sm" key={insight.insight_id}
                onClick={() => { seek(insight.window_end_ms); onSelectInsight?.(insight.insight_id); }}>{clock(insight.window_end_ms)} {insight.title}</button>
            ))}
          </div>
          <details className="observation-details">
            <summary>Observation details</summary>
            <p className="status-line" aria-live="off">
            {tracking.detection_source ?? payload.metadata.detection_source ?? "synthetic"} source
            {" · "}{tracking.players?.filter((p) => !p.inferred).length ?? 0} {tracking.detection_source === "synthetic" ? "simulated players" : "player candidates"}
            {" · "}sample age {sampled.sampleLagMs === null ? "unavailable" : `${Math.round(sampled.sampleLagMs)} ms`}
            {sampled.interpolated ? " · interpolated positions" : ""}
            {sampled.sampleLagMs !== null && sampled.sampleLagMs > 500 ? " · no current observations" : ""}
            </p>
          {tactics ? (
            <details className="tactical-summary">
              <summary>Tactical context and model details</summary>
              <p><strong>Phase:</strong> {(tactics.phase ?? "unknown").replace(/_/g, " ")}
                {" · "}{tactics.chain_count ?? 0} possession chains</p>
              {Object.entries(tactics.team_shapes ?? {}).map(([team, shape]) => (
                <p key={team}>{teams[team] ?? team}: {shape.width_m.toFixed(1)} m wide · {shape.depth_m.toFixed(1)} m deep
                  {" · "}{shape.observed_players} {simulated ? "simulated" : "observed"}
                  {shape.formation_estimate ? ` · ${shape.formation_estimate} estimated shape` : ""}</p>
              ))}
              {calibrated && tactics.lanes?.team_a?.length ? (
                <div className="tactical-lanes" aria-label="Observed tactical channels">
                  {tactics.lanes.team_a.map((lane) => (
                    <div key={lane.lane}>
                      <strong>{lane.lane} channel</strong>
                      <span>{teams.team_a ?? "Home"} {lane.attackers} · {teams.team_b ?? "Away"} {lane.defenders}</span>
                    </div>
                  ))}
                </div>
              ) : null}
              <p className="status-line">Value model: {tactics.value_model?.version ?? "unavailable"}
                {" · "}{tactics.value_model?.status.replace(/_/g, " ") ?? "unavailable"}</p>
              <div className="possession-chains">
                {(tactics.recent_chains ?? []).slice(-4).map((chain) => (
                  <div key={chain.chain_id}>
                    <strong>{teams[chain.team_id] ?? chain.team_id}</strong> {clock(chain.start_ms)}–{clock(chain.end_ms)}
                    {" · "}{chain.actions} actions
                    {chain.progression_m !== null ? ` · ${chain.progression_m.toFixed(1)} m progression` : " · attack direction unknown"}
                    {chain.xt_gain_estimate !== null ? ` · xT estimate ${chain.xt_gain_estimate.toFixed(3)}` : ""}
                    {chain.xg_estimate !== null ? ` · xG estimate ${chain.xg_estimate.toFixed(3)}` : ""}
                  </div>
                ))}
              </div>
            </details>
          ) : null}
          </details>
          {showWarnings && payload.metadata.warnings?.length ? <ul className="analysis-notices">
            {payload.metadata.warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul> : null}
        </>
      ) : null}
    </section>
  );
});
