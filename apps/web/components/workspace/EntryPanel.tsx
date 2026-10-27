import { ArrowRight, FileVideo, FolderOpen, Loader2, Play, Upload, X } from "lucide-react";
import type { ReactNode } from "react";
import Link from "next/link";

export type EntryTab = "demo" | "video" | "saved";
export type EntryPanelProps = {
  tab: EntryTab;
  onTabChange: (tab: EntryTab) => void;
  canAnalyze: boolean;
  publicTrial?: boolean;
  admissionDisabled?: boolean;
  busy: boolean;
  hasMatch: boolean;
  onClose: () => void;
  teamAName: string;
  teamBName: string;
  onTeamAName: (value: string) => void;
  onTeamBName: (value: string) => void;
  scenario: string;
  onScenario: (value: string) => void;
  onStart: () => void;
  file: File | null;
  onFile: (file: File | null) => void;
  url: string;
  onUrl: (value: string) => void;
  rightsConfirmed: boolean;
  onRightsConfirmed: (value: boolean) => void;
  youtubeConfigured?: boolean;
  analysisMode: "events" | "cv";
  onAnalysisMode: (value: "events" | "cv") => void;
  cvOptions: string;
  onCvOptions: (value: string) => void;
  onUpload: () => void;
  onLink: () => void;
  videoStatus: string;
  saved: Array<{ match_id: string; updated_at: string }>;
  onRestore: (id: string) => void;
  children?: ReactNode;
};

function savedDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Saved analysis" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function EntryPanel(props: EntryPanelProps) {
  const tab = props.canAnalyze ? props.tab : "saved";
  let youtube = false;
  try { youtube = ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"].includes(new URL(props.url).hostname.toLowerCase()); }
  catch { /* Incomplete URLs remain editable; the server validates submitted links. */ }
  return (
    <section className={`entry-panel${props.hasMatch ? " entry-panel--compact" : ""}`} aria-label="Start or open a match">
      <div className="entry-panel-header"><h2>Your next perspective.</h2>{props.hasMatch
        ? <button type="button" className="btn btn--quiet btn--sm" aria-label="Close match setup" onClick={props.onClose}><X size={17} /></button>
        : <span className="tag">Workspace</span>}</div>
      <div className="segmented entry-options" role="group" aria-label="Choose how to get started">
        {props.canAnalyze ? <><button type="button" aria-pressed={tab === "demo"} onClick={() => props.onTabChange("demo")}>Try a demo</button>
          <button type="button" aria-pressed={tab === "video"} onClick={() => props.onTabChange("video")}>Your video</button></> : null}
        <button type="button" aria-pressed={tab === "saved"} onClick={() => props.onTabChange("saved")}>Saved matches</button>
      </div>
      {tab !== "saved" ? <div className="team-name-fields">
        <label>Home team name<input maxLength={80} value={props.teamAName} onChange={(event) => props.onTeamAName(event.target.value)} placeholder="Optional team name" disabled={props.busy} /></label>
        <label>Away team name<input maxLength={80} value={props.teamBName} onChange={(event) => props.onTeamBName(event.target.value)} placeholder="Optional team name" disabled={props.busy} /></label>
      </div> : null}
      {tab === "demo" ? (
        <div className="entry-form">
          <div className="field-row"><label htmlFor="scenario">Choose a scenario</label><select id="scenario" value={props.scenario} onChange={(event) => props.onScenario(event.target.value)} disabled={props.busy}>
            <option value="sustained_pressure">Building the pressure</option><option value="rhythm_shift">A change in rhythm</option><option value="player_influence">The influential player</option>
          </select></div>
          <p>A simulated match, real intelligence pipeline. Explore how moments become supported explanations.</p>
          <button type="button" className="btn btn--primary btn--block" onClick={props.onStart} disabled={props.busy || props.admissionDisabled}>
            {props.busy ? <Loader2 size={16} className="icon-spin" aria-hidden="true" /> : <Play size={15} aria-hidden="true" />}
            {props.busy ? "Opening the match..." : "Start live scenario"}<ArrowRight size={16} aria-hidden="true" />
          </button>
          <p className="status-line">Prepares multi-phase play, player trajectories and supported moments. Playback begins when you press Play replay. All data is labelled simulated.</p>
        </div>
      ) : tab === "video" ? (
        <div className="entry-form">
          <div className="field-row"><label htmlFor="analysis-mode">Analysis method</label><select id="analysis-mode" value={props.analysisMode} disabled={props.busy} onChange={(event) => props.onAnalysisMode(event.target.value === "cv" ? "cv" : "events")}>
            <option value="cv">Model-based player and ball tracking</option><option value="events">Development motion preview · no football claims</option>
          </select></div>
          <div className="field-row"><label htmlFor="video-file"><FileVideo size={13} aria-hidden="true" /> Upload video file</label>
            <input id="video-file" type="file" accept="video/*" disabled={props.busy} onChange={(event) => props.onFile(event.target.files?.[0] ?? null)} />
          </div>
          {props.publicTrial ? <p className="status-line">By choosing Upload and analyze, you confirm you have permission to analyze this footage.</p> : null}
          <button type="button" className="btn btn--primary" onClick={props.onUpload} disabled={props.busy || props.admissionDisabled || !props.file}><Upload size={15} aria-hidden="true" />Upload and analyze</button>
          <div className="entry-separator">or a YouTube / direct video link</div>
          <div className="field-row"><label htmlFor="video-url">Video link · YouTube or direct file</label><input id="video-url" type="url" placeholder="YouTube watch, Shorts, or a direct .mp4 link" disabled={props.busy} value={props.url} onChange={(event) => {
            props.onUrl(event.target.value); props.onRightsConfirmed(false);
          }} /></div>
          {youtube ? <label className="rights-confirmation"><input type="checkbox" checked={props.rightsConfirmed} disabled={props.busy}
            onChange={(event) => props.onRightsConfirmed(event.target.checked)} />I have permission to analyze this YouTube video.</label> : null}
          {youtube && props.youtubeConfigured === false ? <p className="error-notice">YouTube import is not configured on this server. Upload an authorized video file instead.</p> : null}
          {props.publicTrial ? <p className="status-line">By choosing Analyze from link, you confirm you have permission to analyze the linked footage.</p> : null}
          <button type="button" className="btn btn--outline" onClick={props.onLink} disabled={props.busy || props.admissionDisabled || !props.url.trim() || youtube && (!props.rightsConfirmed || props.youtubeConfigured === false)}>Analyze from link<ArrowRight size={15} aria-hidden="true" /></button>
          <p className="status-line">Use authorized, accessible footage. Protected, login-required and live YouTube streams are not supported. Model tracking requires a configured local detector; the motion preview cannot support football claims.</p>
          {props.analysisMode === "cv" ? <details className="advanced-panel">
            <summary>Camera calibration and tracking options</summary>
            <div className="sheet-section" style={{ padding: "0 16px 16px" }}>
              <label htmlFor="cv-options">CV options JSON</label><textarea id="cv-options" rows={5} value={props.cvOptions} onChange={(event) => props.onCvOptions(event.target.value)} disabled={props.busy}
                placeholder={'{"sample_fps":8,"attack_directions":{"team_a":1,"team_b":-1},"calibrations":[]}'} />
              <p className="status-line">Use actual surveyed pitch landmarks and optional jersey RGB colours. Without calibration, physical speeds and 2.5D geometry remain unavailable. Team names do not identify jerseys automatically.</p>
            </div>
          </details> : null}
          {props.videoStatus !== "idle" && !props.busy ? <p className="notice notice--quiet" role="status">{props.videoStatus}</p> : null}
        </div>
      ) : (
        <div className="saved-match-list" aria-label="Restore a saved match">
          {props.saved.length ? props.saved.map((match) => <button type="button" className="saved-match" key={match.match_id} onClick={() => props.onRestore(match.match_id)} disabled={props.busy}>
            <span><strong>Analysis {match.match_id.slice(-8)}</strong><small>{savedDate(match.updated_at)}</small></span><FolderOpen size={16} aria-hidden="true" />
          </button>) : <div className="empty-state"><FolderOpen size={24} aria-hidden="true" /><p>No saved matches yet.</p><p className="status-line">{props.canAnalyze ? "Your completed scenarios and analyses will appear here." : "Matches shared with this workspace will appear here."}</p></div>}
        </div>
      )}
      <Link href="/example" prefetch={false} className="btn btn--outline btn--block">Explore an example match</Link>
      {props.children ? <div className="entry-footer">{props.children}</div> : null}
    </section>
  );
}
