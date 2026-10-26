import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Radio, Volume2 } from "lucide-react";
import { clockLabel, readableLabel, type Insight } from "../../lib/workspace-types";
import InsightEvidence from "./InsightEvidence";
import EvidenceClip from "./EvidenceClip";

export default function SelectedMoment({ insight, narrative, title, meta, narrativeInsightId, language, following, onListen, onExplore, overviewAvailable, busy }: {
  insight: Insight | null; narrative: string; title: string; meta: string; narrativeInsightId?: string;
  language: string; following: boolean; onListen: () => void; onExplore: () => void;
  overviewAvailable?: boolean;
  busy?: boolean;
}) {
  const reducedMotion = useReducedMotion();
  const explained = insight && narrativeInsightId === insight.insight_id && Boolean(narrative);
  const source = meta.includes("provider=guardrail-") ? "Evidence guardrail · explanation limited"
    : meta.includes("fallback=true") ? "Fallback explanation · no live model response"
      : meta.includes("provider=foundry") ? "Model explanation · supporting evidence checked"
        : meta && !meta.includes("provider=") ? `Explanation unavailable: ${meta}` : "";
  return (
    <div className="match-explanation">
      <AnimatePresence mode="wait">
        {insight ? <motion.article key={insight.insight_id ?? insight.title} className="focus-insight" aria-label="Current match explanation" data-insight-id={insight.insight_id}
          initial={reducedMotion ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: reducedMotion ? 1 : 0 }} transition={{ duration: reducedMotion ? 0 : .18 }}>
          <div className="insight-hero-top"><span className="insight-eyebrow"><Radio size={13} aria-hidden="true" />{readableLabel(insight.type)}</span><span className="insight-timestamp">{clockLabel(insight.provenance?.window?.end_ms)}</span></div>
          <h2 className="insight-title" lang={explained ? language : "en"} dir="auto">{explained && title ? title : insight.title}</h2>
          <p className="insight-summary" lang={explained ? language : "en"} dir="auto">{explained ? narrative.split("\n\n")[0] : insight.summary}</p>
          {explained && narrative.includes("\n\n") ? <details className="full-explanation"><summary>Read the full breakdown</summary>
            {narrative.split("\n\n").slice(1).map((paragraph, index) => <p key={index} lang={language} dir="auto">{paragraph}</p>)}</details> : null}
          <div className="confidence-row"><span className="confidence-track"><span className="confidence-fill" style={{ width: `${Math.round(insight.confidence * 100)}%` }} /></span><span className="confidence-label">{Math.round(insight.confidence * 100)}% signal confidence</span></div>
          <div className="insight-actions"><button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={onListen}><Volume2 size={14} aria-hidden="true" />Listen</button>
            <button type="button" className="btn btn--outline btn--sm" onClick={onExplore}>Explore moments<ArrowRight size={14} aria-hidden="true" /></button></div>
          {source ? <p className="explanation-source">{source}</p> : null}
          {meta ? <details className="source-details"><summary>Explanation source details</summary><p className="status-line">{meta}</p></details> : null}
          {insight.clip?.status === "ready" && insight.clip.url ? <EvidenceClip path={insight.clip.url} label="Selected moment evidence clip" /> : null}
          <InsightEvidence insight={insight} />
        </motion.article> : <div className="empty-hero"><p className="eyebrow">The intelligence view</p><strong>{overviewAvailable ? "Tracking, with context." : following ? "Following the match." : "No supported moment in this window."}</strong>
          <p>{overviewAvailable ? "Play the replay for synchronized coverage, or listen to an observation overview of the analyzed window. Detections are not automatically verified passes, shots, or tactics."
            : following ? "An explanation will appear when the evidence supports one." : "Explore the available replay or choose another match. Tactiq does not invent missing evidence."}</p>
          {overviewAvailable ? <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={onListen}><Volume2 size={14} aria-hidden="true" />Listen to analysis overview</button> : null}
          <button type="button" className="btn btn--quiet btn--sm" onClick={onExplore}>Explore moments<ArrowRight size={14} aria-hidden="true" /></button>
        </div>}
      </AnimatePresence>
    </div>
  );
}
