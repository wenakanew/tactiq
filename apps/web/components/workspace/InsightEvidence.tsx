import type { Insight } from "../../lib/workspace-types";
import { clockLabel, readableLabel, evidenceMetrics } from "../../lib/workspace-types";

export default function InsightEvidence({ insight }: { insight: Insight }) {
  const metrics = evidenceMetrics(insight);
  return (
    <details className="inline-evidence">
      <summary>Explore the supporting evidence<span aria-hidden="true">+</span></summary>
      <p className="evidence-note">Signals in a defined window, not a claim of independently validated accuracy.</p>
      <div className="evidence-grid">
        <div className="evidence-cell"><span className="metric">Signal confidence</span><span className="value">{Math.round(insight.confidence * 100)}%</span></div>
        <div className="evidence-cell"><span className="metric">Evidence window</span><span className="value">{clockLabel(insight.provenance?.window?.start_ms)}–{clockLabel(insight.provenance?.window?.end_ms)}</span></div>
        {metrics.map((item) => <div className="evidence-cell" key={item.metric}>
          <span className="metric">{readableLabel(item.metric)}</span><span className="value">{item.value}{item.unit ? ` ${item.unit}` : ""}</span>
        </div>)}
      </div>
      {insight.provenance?.reason_code ? <p className="evidence-note">Detection: {readableLabel(insight.provenance.reason_code)}</p> : null}
    </details>
  );
}
