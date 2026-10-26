export default function CommentaryControls({ live, dual, onLive, onDual, status, onStop, showStop }: {
  live: boolean; dual: boolean; onLive: (enabled: boolean) => void; onDual: (enabled: boolean) => void;
  status: string; onStop: () => void; showStop: boolean;
}) {
  return (
    <div className="utility-bar utility-bar--top">
      <div className="commentary-controls">
        <div className="toggle-row">
          <label htmlFor="live-commentary"><input id="live-commentary" type="checkbox" checked={live} onChange={(event) => onLive(event.target.checked)} />Live commentary</label>
          <label htmlFor="dual-commentary"><input id="dual-commentary" type="checkbox" checked={dual} onChange={(event) => onDual(event.target.checked)} />Dual commentary</label>
        </div>
        {status !== "idle" && status !== "stopped" ? <span className="status-line" role="status">{status}</span>
          : <span className="status-line">{dual ? "Lead + analyst perspectives" : "Single-voice narration"}</span>}
      </div>
      {showStop ? <button type="button" className="btn btn--quiet btn--sm" onClick={onStop}>Stop audio</button> : null}
    </div>
  );
}
