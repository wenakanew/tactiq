import { ArrowUpRight } from "lucide-react";
import { clockLabel, readableLabel, type Insight } from "../../lib/workspace-types";
import InsightEvidence from "./InsightEvidence";
import EvidenceClip from "./EvidenceClip";

export default function MomentCard({ insight, selected, onSelect, active }: {
  insight: Insight; selected: boolean; onSelect: () => void; active: boolean;
}) {
  return (
    <article className={`moment-card timeline-item${selected ? " moment-card--selected" : ""}`}>
      <button type="button" className="moment-select" onClick={onSelect} aria-pressed={selected}
        aria-label={`View moment: ${insight.title} at ${clockLabel(insight.provenance?.window?.end_ms)}`}>
        <span className="moment-time">{clockLabel(insight.provenance?.window?.end_ms)}</span>
        <span className="moment-copy"><strong>{insight.title}</strong><span className="moment-description">{insight.summary}</span>
          <span className="moment-tags"><span className="tag">{readableLabel(insight.type)}</span><span className="tag">{Math.round(insight.confidence * 100)}% signal confidence</span>
            {insight.clip?.status === "ready" ? <span className="tag tag--accent">Evidence clip</span> : null}</span>
        </span><ArrowUpRight size={18} aria-hidden="true" />
      </button>
      <div className="moment-inline">
        {insight.clip?.status === "ready" && insight.clip.url ? <EvidenceClip path={insight.clip.url} label={`Evidence clip: ${insight.title}`} active={active} /> : null}
        <InsightEvidence insight={insight} />
      </div>
    </article>
  );
}
